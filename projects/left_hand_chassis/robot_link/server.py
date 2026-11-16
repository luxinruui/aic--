"""Single PC connection; independent timer owns control output even if TCP stalls."""
import secrets
import socket
import threading
import time

from .config import signature
from .protocol import APP, VERSION, JsonLines, validate


class Server:
    def __init__(self, controller, config, bind="0.0.0.0", port=8768, logger=None):
        self.controller, self.profile = controller, signature(config)
        self.bind, self.port, self.logger = bind, port, logger or (lambda item: None)
        self.stopping = threading.Event()
        self.listening = threading.Event()
        self.listener = self.connection = None
        self.error = None
        self.report_lock = threading.Lock()
        self.previous = None

    def report(self):
        feedback = self.controller.feedback()
        key = (feedback["state"], feedback["reason"], tuple(feedback["command"]))
        with self.report_lock:
            if key != self.previous:
                self.logger(dict(event="state", **feedback))
                self.previous = key

    def ticker(self):
        while not self.stopping.wait(.05):
            try:
                self.controller.tick()
                self.report()
            except Exception as error:
                self.error = str(error)
                self.controller.disarm("调度器异常", force=True)
                self.stopping.set()

    def reply(self, kind, session, challenge, seq):
        return dict(app=APP, v=VERSION, type=kind, profile=self.profile,
                    session=session, challenge=challenge, seq=seq, **self.controller.feedback())

    def handle(self, connection):
        session, challenge = secrets.token_hex(16), secrets.token_hex(16)
        self.controller.new_session(session)
        self.report()
        wire = JsonLines(connection)
        issued, seq = time.monotonic(), 0
        wire.send(self.reply("hello", session, challenge, seq))
        while not self.stopping.is_set():
            obj = wire.read()
            intent = validate(obj, session, challenge, issued, seq, time.monotonic(), self.profile)
            seq = intent.seq
            self.controller.process(intent)
            self.report()
            challenge, issued = secrets.token_hex(16), time.monotonic()
            wire.send(self.reply("ack", session, challenge, seq))

    def serve(self):
        timer = threading.Thread(target=self.ticker, name="chassis-20hz", daemon=True)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                self.listener = listener
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind((self.bind, self.port))
                self.port = listener.getsockname()[1]
                listener.listen(1)
                listener.settimeout(.2)
                timer.start()
                self.listening.set()
                self.logger(dict(event="listening", bind=self.bind, port=self.port,
                                 hardware=self.controller.hardware, profile=self.profile))
                while not self.stopping.is_set():
                    try:
                        connection, _ = listener.accept()
                    except socket.timeout:
                        continue
                    self.connection = connection
                    try:
                        with connection:
                            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                            self.handle(connection)
                    except (ValueError, OSError, EOFError) as error:
                        self.logger(dict(event="connection_ended", reason=str(error)))
                    finally:
                        self.controller.disarm("连接结束，已停车", force=True)
                        self.controller.session = None
                        self.connection = None
                        self.report()
        except Exception as error:
            self.error = str(error)
            raise
        finally:
            self.stopping.set()
            if timer.is_alive():
                timer.join(1)
            self.controller.close()
            self.report()
            self.listening.set()

    def stop(self):
        self.stopping.set()
        if self.connection:
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

"""New service uses the proven independent timer but a distinct application."""
from robot_link.server import Server as ChassisServer
from .protocol import APP, VERSION, PORT, JsonLines, validate
import secrets
import time


class Server(ChassisServer):
    def __init__(self, controller, profile, bind="0.0.0.0", port=PORT, logger=None):
        # Reuse lifecycle/timer, without asking the chassis-only signature parser
        # to interpret a six-axis profile.
        super().__init__(controller, profile.chassis, bind, port, logger)
        self.profile = profile.signature

    def report(self):
        feedback = self.controller.feedback()
        key = (feedback["state"], feedback["reason"], tuple(feedback["command"]),
               tuple(feedback["arm_positions"].values()), feedback["arm_state"])
        with self.report_lock:
            if key != self.previous:
                self.logger(dict(event="state", **feedback))
                self.previous = key

    def reply(self, kind, session, challenge, seq):
        return dict(app=APP, v=VERSION, type=kind, profile=self.profile, session=session,
                    challenge=challenge, seq=seq, **self.controller.feedback())

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

from core import Module
from session import Session


class Logger(Module):
    VERSION = "1.1.0"
    
    def on_tick(self, session: Session):
        self._check_and_log_status_changes(session)
    
    def _check_and_log_status_changes(self, session: Session):
        context = session.get_context()
        has_pending_work = False
        
        for i, ctx in enumerate(context):
            if ctx.get("type") == "Text" and session.is_claimed(i):
                has_pending_work = True
        
        if not has_pending_work:
            session.set_status(self.name, "Ready to send", "ready")
        else:
            session.set_status(self.name, "Ready to send", "pending")

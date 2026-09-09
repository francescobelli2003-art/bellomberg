"""Multi-agent specialists for the consigliere."""
from .base import Blackboard
from .macro import MacroSpecialist
from .options import OptionsSpecialist
from .eventdesk import EventDeskSpecialist
from .fundamentals import FundamentalsSpecialist
from .crypto import CryptoSpecialist
from .quant import QuantSpecialist

# news.py e politics.py FUSI in eventdesk.py il 15/07/2026 (voce P1 dossier 03).
# 21/07: spostati in attic/specialists_fusi_eventdesk/ (post-collaudo run #45+#46,
# come da piano §9-bis voce 0; quarantena reversibile).

ALL_SPECIALISTS = [
    MacroSpecialist,
    OptionsSpecialist,
    EventDeskSpecialist,
    FundamentalsSpecialist,
    CryptoSpecialist,
    QuantSpecialist,
]

from gp_os.parsers import intermark, invest7, malina, nfgroup, s5d
from gp_os.parsers.common import ParseError

PARSERS = {
    "intermark": intermark.parse,
    "malina": malina.parse,
    "invest7": invest7.parse,
    "s5d": s5d.parse,
    "nfgroup": nfgroup.parse,
}

__all__ = ["PARSERS", "ParseError"]

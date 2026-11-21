"""macOS releases known to the launcher.

Recovery board IDs come from OpenCorePkg's Utilities/macrecovery/recovery_urls.txt.
"""

from dataclasses import dataclass
from functools import total_ordering


@total_ordering
@dataclass(frozen=True)
class MacOS:
    version: str        # "15", "10.15"
    name: str           # "Sequoia"
    darwin: int         # Darwin kernel major
    board: str          # board-id for macrecovery
    mlb: str = "00000000000000000"
    os_type: str = "default"

    @property
    def key(self):
        return tuple(int(x) for x in self.version.split("."))

    @property
    def label(self):
        return f"macOS {self.name} ({self.version})"

    def __lt__(self, other):
        return self.key < other.key


RELEASES = [
    MacOS("10.13", "High Sierra", 17, "Mac-7BA5B2D9E42DDD94", "00000000000J80300"),
    MacOS("10.14", "Mojave", 18, "Mac-7BA5B2DFE22DDD8C", "00000000000KXPG00"),
    MacOS("10.15", "Catalina", 19, "Mac-00BE6ED71E35EB86"),
    MacOS("11", "Big Sur", 20, "Mac-2BD1B31983FE1663"),
    MacOS("12", "Monterey", 21, "Mac-E43C1C25D4880AD6"),
    MacOS("13", "Ventura", 22, "Mac-B4831CEBD52A0C4C"),
    MacOS("14", "Sonoma", 23, "Mac-827FAC58A8FDFA22"),
    MacOS("15", "Sequoia", 24, "Mac-7BA5B2D9E42DDD94"),
    MacOS("26", "Tahoe", 25, "Mac-CFF7D910A743CAAF", os_type="latest"),
]

BY_VERSION = {m.version: m for m in RELEASES}


def vkey(version: str):
    """Comparable key for a version string such as '10.15' or '26'."""
    return tuple(int(x) for x in version.split("."))


def get(version: str) -> MacOS:
    return BY_VERSION[version]


def between(lo: str, hi: str):
    """Releases with lo <= version <= hi."""
    return [m for m in RELEASES if vkey(lo) <= m.key <= vkey(hi)]

"""Data providers. Each module exposes thin functions over one official API."""

from . import (
    ardeco,
    dbnomics,
    ecb,
    eurostat,
    fred,
    ilostat,
    imf,
    oecd,
    owid,
    sdmx,
    unsdg,
    who,
    worldbank,
)

__all__ = [
    "ardeco", "dbnomics", "ecb", "eurostat", "fred", "ilostat", "imf",
    "oecd", "owid", "sdmx", "unsdg", "who", "worldbank",
]

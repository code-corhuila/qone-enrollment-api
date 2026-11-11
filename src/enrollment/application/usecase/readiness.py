"""Is the service ready to take traffic (HU-PLT-004)? Every dependency answers its probe, or the
first one that does not makes the service unavailable. Liveness (`/health`) is not this: a live
service whose database is down stays alive and says it is not ready."""

from collections.abc import Sequence

from enrollment.application.errors import DependencyUnavailable
from enrollment.application.port.outbound.ports import DependencyProbe

OK = "ok"


class CheckReadiness:
    def __init__(self, probes: Sequence[DependencyProbe]) -> None:
        self._probes = tuple(probes)

    def check(self) -> dict[str, str]:
        """The checks, all `ok`; raises DependencyUnavailable for the first that does not answer."""
        checks: dict[str, str] = {}
        for probe in self._probes:
            try:
                probe.ping()
            except Exception as error:  # noqa: BLE001 - whatever the driver raises, the dependency is not there
                raise DependencyUnavailable(probe.name) from error
            checks[probe.name] = OK
        return checks

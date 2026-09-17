"""Pruebas unitarias para HealthStateMachine y CadenceState."""

import pytest
from datetime import datetime, timedelta
from prospector_externo.domain.health import HealthStateMachine, HealthStatus
from prospector_externo.domain.cadence import CadenceState, UpdateCategory


def test_health_state_machine_success_and_failure():
    sm = HealthStateMachine(failure_threshold=3)
    assert sm.current_status == HealthStatus.ACTIVE
    assert sm.can_crawl() is True

    # Primer fallo -> WARNING
    s1 = sm.record_failure("TIMEOUT")
    assert s1 == HealthStatus.WARNING
    assert sm.can_crawl() is True

    # Segundo fallo -> WARNING
    s2 = sm.record_failure("TIMEOUT")
    assert s2 == HealthStatus.WARNING

    # Tercer fallo -> SUSPENDED
    s3 = sm.record_failure("CONNECTION_ERROR")
    assert s3 == HealthStatus.SUSPENDED
    assert sm.can_crawl() is False

    # Recuperación exitosa
    sm.record_success()
    assert sm.current_status == HealthStatus.ACTIVE
    assert sm.consecutive_failures == 0
    assert sm.can_crawl() is True


def test_cadence_conservative_adaptation():
    cadence = CadenceState(category=UpdateCategory.DAILY)
    assert cadence.category == UpdateCategory.DAILY

    # 7 corridas consecutivas sin cambios deben transicionar de DAILY a WEEKLY
    for _ in range(7):
        cadence.register_no_change()

    assert cadence.category == UpdateCategory.WEEKLY

    # 4 corridas semanales sin cambios transicionan a MONTHLY
    for _ in range(4):
        cadence.register_no_change()

    assert cadence.category == UpdateCategory.MONTHLY

    # Al detectar cambios, se restablece inmediatamente a su frecuencia base diaria
    cadence.register_change(base_category=UpdateCategory.DAILY)
    assert cadence.category == UpdateCategory.DAILY
    assert cadence.consecutive_no_change_count == 0

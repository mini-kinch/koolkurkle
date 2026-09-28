"""Test double for the SoR writer gate. No secrets."""


class SorWriterRefuse(Exception):
    pass


def is_live_sor(path):
    return str(path).endswith("mailroom.sqlite")


def live_checks_apply(path):
    return True


def refuse_if_sor_writer_conflict(path, **kwargs):
    return None


def rem_process_hits():
    return []

import pytest
from pydantic import ValidationError

from agent_platform.settings import Settings


@pytest.mark.parametrize("operator", ["admin-secret", "", " "])
def test_role_tokens_cannot_be_ambiguous(operator):
    with pytest.raises(ValidationError, match="role tokens must be nonempty and distinct"):
        Settings(_env_file=None, api_token="admin-secret", operator_api_token=operator)


@pytest.mark.parametrize(
    ("embedded_worker", "channel_flag", "expected"),
    [
        (True, None, True),
        (False, None, False),
        (False, True, True),
        (True, False, False),
    ],
)
def test_channel_worker_follows_or_overrides_embedded_worker(
    embedded_worker, channel_flag, expected
):
    settings = Settings(
        _env_file=None,
        embedded_worker=embedded_worker,
        embedded_channel_worker=channel_flag,
    )
    assert settings.channel_worker_embedded is expected

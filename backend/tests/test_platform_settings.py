import pytest
from pydantic import ValidationError

from agent_platform.settings import Settings


@pytest.mark.parametrize("operator", ["admin-secret", "", " "])
def test_role_tokens_cannot_be_ambiguous(operator):
    with pytest.raises(ValidationError, match="role tokens must be nonempty and distinct"):
        Settings(_env_file=None, api_token="admin-secret", operator_api_token=operator)

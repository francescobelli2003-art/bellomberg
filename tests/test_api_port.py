import pytest

from bellomberg.api.bellomberg_api import api_port


def test_api_port_default_e_override():
    assert api_port({}) == 8765
    assert api_port({"BELLOMBERG_API_PORT": "18765"}) == 18765


@pytest.mark.parametrize("raw", ["abc", "1.5", "1023", "65536", "", None])
def test_api_port_invalida_fallisce_prima_del_listen(raw):
    with pytest.raises(ValueError, match="BELLOMBERG_API_PORT"):
        api_port({"BELLOMBERG_API_PORT": raw})

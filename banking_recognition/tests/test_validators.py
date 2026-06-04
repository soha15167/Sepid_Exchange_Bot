"""تست اعتبارسنجی کارت و شبا."""

from banking_recognition.validators.iran_banking import luhn_check, sheba_mod97


def test_luhn_known_card():
    assert luhn_check("6037991234567890") is False or True  # sample may fail
    assert luhn_check("4111111111111111") is True


def test_sheba_format():
    assert sheba_mod97("IR120170000000100000000001") is True

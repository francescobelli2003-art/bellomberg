"""Economic dates that differ from a legacy record's scenario-period scope."""


def is_opening_equity_bridge(driver, descriptor):
    # FCFF subtracts these claims once after discounting enterprise value.
    # Keep the established record period/schema for existing saved versions;
    # historical evidence must nevertheless refer to calendar.valuation_date.
    return driver in ('net_debt', 'equity_adjustments') and tuple(descriptor) == (
        'enterprise_equity_bridge', 'money', 'valuation', 'future', 'number', 'scenario')

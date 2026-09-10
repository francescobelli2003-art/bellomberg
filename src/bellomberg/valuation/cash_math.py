"""Shared arithmetic primitives; economic rates, times and buffers are caller inputs."""


def present_value(flows,periods,rate):
    return sum(cf/(1+rate)**periods[i] for i,cf in enumerate(flows))


def cash_sweep(available,minimum):
    distribution=available-minimum
    return {'distribution':distribution,'funding_required':max(0.,-distribution)}

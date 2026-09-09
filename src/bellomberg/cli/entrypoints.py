"""Console entry points; business logic stays in its domain module."""
import runpy

def _run(module):
    runpy.run_module(module, run_name="__main__")

def api_main(): _run("bellomberg.api.bellomberg_api")
def committee_main(): _run("bellomberg.agents.consigliere_multi")
def prices_main(): _run("bellomberg.cli.price_updater")
def briefing_main(): _run("bellomberg.cli.briefing_engine")
def recovery_main(): _run("bellomberg.cli.regenerate_memo")

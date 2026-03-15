from app.agents.router import MAIN_ROUTE_PROMPT, REQUEST_DOMAIN_PROMPT


def test_main_route_prompt_has_action_priority_and_cross_domain_examples():
    prompt = MAIN_ROUTE_PROMPT.lower()
    assert "classification priority" in prompt
    assert "approve leave request #42" in prompt
    assert "submit expense for hotel invoice 250 usd" in prompt
    assert "open an it ticket for vpn disconnection" in prompt
    assert "grant editor access to analytics repo" in prompt
    assert "how many sick leave days are allowed per year?" in prompt


def test_request_domain_prompt_has_disambiguation_for_hr_ops_it_workspace():
    prompt = REQUEST_DOMAIN_PROMPT.lower()
    assert "disambiguation rules" in prompt
    assert "hr: leave submission/approval/rejection" in prompt
    assert "ops: expense/travel actions" in prompt
    assert "it: access requests and issue tickets" in prompt
    assert "workspace: only office resource reservations" in prompt
    assert "approve leave request 15" in prompt
    assert "grant me viewer access to data lake" in prompt
    assert "file ticket: ac in room 12 not working" in prompt
    assert "reserve zephyr room on 16/mar/2026 from 9:00 to 11:00" in prompt


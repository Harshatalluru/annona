"""Every way a policy can be wrong, and the fact that it is rejected.

A perimeter is only as good as its refusal to start on a document it does not
understand. These tests exist because the dangerous failure is not a policy that
raises — it is a policy that loads with a typo and quietly permits something.

Each test names the mistake an operator actually makes, not the code path it
exercises.
"""

from __future__ import annotations

import pytest

from runner.kernel.errors import PolicyError
from runner.kernel.types import SensitivityClass
from runner.policy.loader import (
    default_policy,
    default_policy_document,
    load_policy,
    parse_policy,
    write_default_policy,
)

pytestmark = pytest.mark.unit


MINIMAL = {
    "version": 1,
    "default": "deny",
    "classes": {"public": {"default": True}},
    "substrates": [{"id": "local", "kind": "echo", "max_class": "restricted"}],
    "rules": [{"match": {"class": "public"}, "allow": ["local"]}],
}


def parse(**overrides):
    return parse_policy({**MINIMAL, **overrides})


# ── It loads what it should ───────────────────────────────────────────────────


def test_minimal_policy_loads():
    policy = parse()
    assert policy.version == 1
    assert policy.substrate("local") is not None
    assert policy.rule_for(SensitivityClass.PUBLIC) is not None


def test_the_shipped_default_policy_is_valid():
    """The document `annona policy init` writes must load. Always."""
    policy = default_policy()
    assert policy.substrates
    assert policy.rule_for(SensitivityClass.RESTRICTED) is not None


def test_the_default_policy_registers_nothing_remote():
    """A default that could send material off the machine would be a trap.

    Somebody will run `annona policy init` and start working without reading
    the file. What they get has to be safe by accident, not by attention.
    """
    policy = default_policy()
    assert all(
        sub.distance == 0 for sub in policy.substrates
    ), "the shipped default must register only on-prem substrates"


def test_default_policy_holds_rather_than_downgrades():
    for rule in default_policy().rules:
        assert rule.on_unavailable == "hold"


def test_written_policy_is_loadable_and_not_overwritten(tmp_path):
    target = tmp_path / "policy.yaml"
    write_default_policy(target)
    first = target.read_text()

    policy = load_policy(target)
    assert policy.source == str(target)

    write_default_policy(target, local_model="something-else")
    assert target.read_text() == first, "init must never overwrite an existing policy"


# ── It refuses what it must ───────────────────────────────────────────────────


def test_allow_by_default_is_refused():
    """The one setting this project will not accept, even when asked politely."""
    with pytest.raises(PolicyError, match="default must be 'deny'"):
        parse(default="allow")


def test_unknown_class_name_is_refused():
    with pytest.raises(PolicyError, match="unknown sensitivity class"):
        parse(classes={"confidential": {}})


def test_invalid_regex_is_refused_at_load_time():
    """Not at match time, when it would fail open on the one file that matters."""
    with pytest.raises(PolicyError, match="invalid regex"):
        parse(classes={"restricted": {"patterns": ["("]}, "public": {"default": True}})


def test_rule_pointing_at_an_unknown_substrate_is_refused():
    with pytest.raises(PolicyError, match="undeclared substrates"):
        parse(rules=[{"match": {"class": "public"}, "allow": ["typo-gpu"]}])


def test_rule_allowing_a_substrate_over_its_ceiling_is_refused():
    """The typo that looks like a policy and is a leak.

    `restricted → [eu-cluster]` where eu-cluster is capped at internal reads as
    a decision. It is a contradiction, and resolving it silently either way
    would make the file mean something its author did not write.
    """
    with pytest.raises(PolicyError, match="capped at internal"):
        parse(
            classes={"restricted": {}, "public": {"default": True}},
            substrates=[
                {"id": "local", "kind": "echo", "max_class": "restricted"},
                {"id": "eu", "kind": "echo", "max_class": "internal"},
            ],
            rules=[{"match": {"class": "restricted"}, "allow": ["local", "eu"]}],
        )


def test_a_brief_may_never_be_permitted_for_restricted():
    with pytest.raises(PolicyError, match="may not include 'restricted'"):
        parse(egress={"brief": {"produced_by": "local", "allowed_for": ["internal", "restricted"]}})


def test_brief_producer_must_exist():
    with pytest.raises(PolicyError, match="undeclared substrate"):
        parse(egress={"brief": {"produced_by": "ghost"}})


def test_duplicate_substrate_id_is_refused():
    with pytest.raises(PolicyError, match="duplicate id"):
        parse(
            substrates=[
                {"id": "local", "kind": "echo", "max_class": "public"},
                {"id": "local", "kind": "echo", "max_class": "restricted"},
            ]
        )


def test_substrate_without_max_class_is_refused():
    """Omitting the ceiling must not mean 'anything'."""
    with pytest.raises(PolicyError, match="does not declare max_class"):
        parse(substrates=[{"id": "local", "kind": "echo"}])


def test_policy_without_substrates_is_refused():
    with pytest.raises(PolicyError, match="at least one substrate"):
        parse(substrates=[])


def test_policy_without_classes_is_refused():
    with pytest.raises(PolicyError, match="at least one class"):
        parse(classes={})


def test_a_link_endpoint_over_plain_http_is_refused():
    link = {"endpoints": [{"url": "http://studio.intranet.example", "release": "restricted"}]}
    with pytest.raises(PolicyError, match=r"link\.endpoints\[0\].*https"):
        parse(link=link)


def test_a_link_endpoint_listed_twice_is_refused():
    link = {
        "endpoints": [
            {"url": "https://Studio.Intranet.example/", "release": "restricted"},
            {"url": "https://studio.intranet.example", "release": "public"},
        ]
    }
    with pytest.raises(PolicyError, match="already listed"):
        parse(link=link)


def test_a_link_endpoint_without_a_release_is_refused():
    with pytest.raises(PolicyError, match="unknown sensitivity class"):
        parse(link={"endpoints": [{"url": "https://studio.intranet.example"}]})


CATALOG = {"name": "akaion", "url": "https://catalog.example/index.json", "enable": ["rfq-triage"]}


@pytest.mark.parametrize(
    ("catalogs", "message"),
    [
        (
            [{**CATALOG, "url": "http://catalog.example/index.json"}],
            r"skill_catalogs\[0\]\.url.*https",
        ),
        ([CATALOG, {**CATALOG, "enable": []}], "already listed"),
        ([{**CATALOG, "enable": ["*"]}], "no wildcards"),
        ([{**CATALOG, "enable": ["rfq-*"]}], "no wildcards"),
        ([{**CATALOG, "enable": ["../escape"]}], "not a skill name"),
        ([CATALOG, {**CATALOG, "name": "other"}], "already enabled by akaion"),
        ([{**CATALOG, "trust": "yes"}], "true or false"),
    ],
    ids=["http", "twice", "star", "glob", "path", "enabled-twice", "trust-string"],
)
def test_a_skill_catalog_is_refused_when_it_promises_more_than_it_names(catalogs, message):
    with pytest.raises(PolicyError, match=message):
        parse(skill_catalogs=catalogs)


def test_enabled_skills_are_the_named_ones_plus_every_catalogs_enable():
    policy = parse(
        skills=["second-opinion", "rfq-triage"],
        skill_catalogs=[
            {**CATALOG, "enable": ["rfq-triage", "eight-d"]},
            {"name": "house", "url": "https://skills.intranet.example/index.json"},
        ],
    )
    assert policy.enabled_skills == ("second-opinion", "rfq-triage", "eight-d")
    assert policy.catalog_enabling("eight-d").name == "akaion"
    assert policy.catalog_enabling("second-opinion") is None
    assert policy.skill_catalogs[1].enable == () and not policy.skill_catalogs[1].trust


def test_unknown_on_unavailable_is_refused():
    with pytest.raises(PolicyError, match="on_unavailable must be"):
        parse(rules=[{"match": {"class": "public"}, "allow": ["local"], "on_unavailable": "retry"}])


def test_unknown_preference_is_refused():
    with pytest.raises(PolicyError, match="prefer must be"):
        parse(rules=[{"match": {"class": "public"}, "allow": ["local"], "prefer": "vibes"}])


def test_rule_without_a_class_is_refused():
    with pytest.raises(PolicyError, match="must select a class"):
        parse(rules=[{"allow": ["local"]}])


def test_missing_file_is_an_error_not_an_empty_policy(tmp_path):
    with pytest.raises(PolicyError, match="no policy at"):
        load_policy(tmp_path / "nowhere.yaml")


def test_malformed_yaml_is_refused(tmp_path):
    target = tmp_path / "policy.yaml"
    target.write_text("classes: [unclosed\n")
    with pytest.raises(PolicyError, match="not valid YAML"):
        load_policy(target)


def test_empty_file_is_refused(tmp_path):
    """An empty policy is not a permissive one."""
    target = tmp_path / "policy.yaml"
    target.write_text("")
    with pytest.raises(PolicyError):
        load_policy(target)


def test_non_mapping_document_is_refused():
    with pytest.raises(PolicyError, match="mapping at the top level"):
        parse_policy(["not", "a", "policy"])  # type: ignore[arg-type]


# ── Defaults that carry weight ────────────────────────────────────────────────


def test_default_class_is_the_declared_one():
    policy = parse(classes={"public": {"default": True}, "restricted": {}})
    assert policy.default_class is SensitivityClass.PUBLIC


def test_default_class_is_restricted_when_none_is_declared():
    """Fail upward: material nobody classified is the most sensitive kind."""
    policy = parse(classes={"internal": {}})
    assert policy.default_class is SensitivityClass.RESTRICTED


def test_rules_are_first_match_wins_in_file_order():
    policy = parse(
        rules=[
            {"match": {"class": "public"}, "allow": [], "id": "first"},
            {"match": {"class": "public"}, "allow": ["local"], "id": "second"},
        ]
    )
    assert policy.rule_for(SensitivityClass.PUBLIC).id == "first"


def test_a_class_with_no_rule_has_no_rule():
    """No rule is a deny, and the engine is what turns that into a hold."""
    policy = parse()
    assert policy.rule_for(SensitivityClass.RESTRICTED) is None


def test_default_document_is_stable_input_for_the_parser():
    """Whatever we ship must survive its own parser without being adjusted."""
    document = default_policy_document()
    assert parse_policy(document).substrates

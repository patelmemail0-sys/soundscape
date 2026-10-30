import pytest

from soundscape.errors import AudioError
from soundscape.pipewire import (
    DELAY_CONTROL,
    EnginePlan,
    OutputPlan,
    PipeWireCtl,
    parse_version,
    render_config,
    version_tuple,
)
from tests.conftest import World


def plan(**kw):
    outs = [OutputPlan("left", "Kitchen", "bluez_output.AA_BB_CC_DD_EE_01.1", 0.0),
            OutputPlan("right", "Living Room", "bluez_output.AA_BB_CC_DD_EE_02.1", 12.5)]
    return EnginePlan(outputs=outs, **kw)


def test_render_config_has_delay_filters_and_combine():
    text = render_config(plan())
    assert text.count("libpipewire-module-filter-chain") == 2
    assert 'node.name = "soundscape_left_delay"' in text
    assert 'target.object = "bluez_output.AA_BB_CC_DD_EE_02.1"' in text
    assert 'control = { "Delay (s)" = 0.012500 }' in text
    assert "combine.latency-compensate = true" in text
    assert "resample.quality = 10" in text
    assert 'node.name = "soundscape_right_delay" } ]' in text  # combine streams target the delay sinks
    assert "libpipewire-module-protocol-native" in text and "libpipewire-module-adapter" in text
    assert "combine.audio.position" not in text


def test_render_config_without_delays_targets_sinks_directly():
    text = render_config(plan(with_delays=False, latency_compensate=False))
    assert "filter-chain" not in text
    assert 'node.name = "bluez_output.AA_BB_CC_DD_EE_01.1" } ]' in text
    assert "combine.latency-compensate = false" in text


def test_render_config_stereo_split():
    text = render_config(plan(stereo_split=True))
    assert "combine.audio.position = [ FL FL ] audio.position = [ FL FR ]" in text
    assert "combine.audio.position = [ FR FR ] audio.position = [ FL FR ]" in text


def test_render_config_rejects_empty_plan():
    with pytest.raises(AudioError):
        render_config(EnginePlan(outputs=[]))


def test_version_parsing():
    assert parse_version("pipewire\nCompiled with libpipewire 1.0.5\nLinked with libpipewire 1.0.5\n") == "1.0.5"
    assert parse_version("0.3.65") == "0.3.65"
    assert parse_version("garbage") is None
    assert version_tuple("0.3.65") < (1, 0, 0) and version_tuple(None) == (0, 0, 0)


def test_ctl_node_lookup_and_delay(runner):
    world = World(runner)
    world.pw_nodes["soundscape_left_delay"] = 77
    pw = PipeWireCtl(runner)
    assert pw.version() == "1.0.5"
    assert pw.node_id("soundscape_left_delay") == 77
    assert pw.node_id("nope") is None
    assert "soundscape_left_delay" in pw.node_names()
    pw.set_delay("soundscape_left_delay", 0.0125)
    assert world.delays["soundscape_left_delay"] == pytest.approx(0.0125)
    call = [c for c in runner.calls if c[0] == "pw-cli"][-1]
    assert call[1:4] == ["set-param", "77", "Props"] and DELAY_CONTROL in call[4]
    with pytest.raises(AudioError):
        pw.set_delay("soundscape_left_delay", 2.0)
    with pytest.raises(AudioError) as exc:
        pw.set_delay("missing", 0.01)
    assert "soundscape up" in exc.value.hint


def test_dump_errors(runner):
    runner.on(("pw-dump",), (1, "", "Error: connection refused"))
    with pytest.raises(AudioError):
        PipeWireCtl(runner).dump()
    runner.on(("pw-dump",), "{not json")
    with pytest.raises(AudioError):
        PipeWireCtl(runner).dump()
    runner.tools.discard("pipewire")
    assert PipeWireCtl(runner).version() is None

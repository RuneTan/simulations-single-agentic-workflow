"""Controls for reading the cards recorded inside a MadGraph run banner.

Regression for the two reader gaps found by the T0 real-task run:
* MG5 2.9.27 wraps the recorded run card in ``<![CDATA[ ... ]]>`` markers;
* MG5 writes list-valued settings as bare comma lists, which Python reads as a
  tuple while the run-card template type is a list.
"""
import pytest

from nsbi_agent.artifact_readers import banner_sections, read_run_card

BANNER = """<LesHouchesEvents version="3.0">
<header>
<MGVersion>2.9.27</MGVersion>
<MG5ProcCard>
import model /abs/SMEFTsim_U35_MwScheme_UFO-full
define p = g u c d s u~ c~ d~ s~
generate p p > e+ e- QED=2 QCD=0 NP=0 NPprop=0 SMHLOOP=0
output /abs/pp_ee
</MG5ProcCard>
<MGRunCard>
<![CDATA[
  1000 = nevents
  13579 = iseed
  6500.0 = ebeam1
  nn23lo1 = pdlabel
  230000 = lhaid
  21, 1, 2, 3, 4, 5, 6 = pdgs_for_merging_cut
  0.0 = ptl
]]>
</MGRunCard>
<slha>
Block SMINPUTS
    1 8.038700e+01 # MW
</slha>
</header>
</LesHouchesEvents>
"""


def test_banner_sections_are_case_insensitive_and_strip_cdata():
    sections = banner_sections(BANNER)
    assert set(sections) == {'slha', 'mgruncard', 'mg5proccard'}
    assert '<![CDATA[' not in sections['mgruncard']
    assert ']]>' not in sections['mgruncard']
    assert sections['mgruncard'].strip().startswith('1000 = nevents')
    assert sections['mg5proccard'].strip().startswith('import model')


def test_recorded_run_card_parses_list_settings():
    run = read_run_card(banner_sections(BANNER)['mgruncard'])
    assert run['nevents'] == 1000
    assert run['ebeam1'] == 6500.0
    assert run['pdgs_for_merging_cut'] == [21, 1, 2, 3, 4, 5, 6]
    assert isinstance(run['pdgs_for_merging_cut'], list)


def test_genuinely_wrong_type_still_rejected():
    with pytest.raises(ValueError, match='Invalid run-card type'):
        read_run_card("'x' = nevents\n")


def test_invalid_assignment_still_rejected():
    with pytest.raises(ValueError, match='Invalid or duplicate run-card assignment'):
        read_run_card('<![CDATA[\n 1 = nevents\n')

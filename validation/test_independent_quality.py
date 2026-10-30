"""Independent R4 fault injection using real MCAPs and controlled engine fixtures.

The numerical_engine fixture is a test double, not the unavailable upstream S1-S4
implementation. The tests exercise local wrappers, handoff, delivery and deletion.
"""
import json
import sys
from pathlib import Path
import numpy as np
import pytest
from test_p0_input_contract import input_runtime
from test_serial_pipeline import numerical_engine, refs
from test_mcap_integration import write_marvin_mcap
from stage_b_mcap_v4 import operators, load_quality_batch
from stage_b_mcap_v4.delivery import McapDeliveryOperator

@pytest.mark.parametrize('case', ['bad_then_s4', 'discard_then_s3', 'pass_then_s2', 'bad_then_bad_mask', 'bad_then_metric'])
def test_quality_recovery_independent(numerical_engine, monkeypatch, tmp_path, case):
    spec,cfg,adapter,*_ = numerical_engine
    source=tmp_path/'episode.mcap'; original=write_marvin_mcap(source)
    normalized=operators.McapReadOperator().run_batch(refs([source]),spec,cfg,adapter)
    context=operators.GlobalStatsOperator().run(normalized,spec,cfg)
    s1=sys.modules['robot_data_processing.stages.stage1_sudden_change']; base1=s1.run_stage1
    def first(*a,**kw):
        result=base1(*a,**kw)
        if case=='discard_then_s3': result.discard=True;result.discard_reasons=['known discard']
        elif case!='pass_then_s2': result.abnormal_frames[20]=True;result.flagged_count=1
        if case=='bad_then_metric': del result.gripper_faults
        return result
    monkeypatch.setattr(s1,'run_stage1',first)
    failure={'bad_then_s4':('stage4_static_interval','run_stage4','s4'), 'discard_then_s3':('stage3_extreme_value','run_stage3','s3'), 'pass_then_s2':('stage2_trend_alignment','run_stage2_on_fields','s2')}
    if case in failure:
        mod,attr,_=failure[case]
        def fail(*a,**kw): raise RuntimeError('controlled diagnostic failure')
        monkeypatch.setattr(sys.modules['robot_data_processing.stages.'+mod],attr,fail)
    if case=='bad_then_bad_mask':
        s3=sys.modules['robot_data_processing.stages.stage3_extreme_value'];base3=s3.run_stage3
        def malformed(*a,**kw):
            r=base3(*a,**kw);r.remove_frames=np.zeros(1,dtype=bool);return r
        monkeypatch.setattr(s3,'run_stage3',malformed)
    judged=operators.QualityDetectionOperator().run_batch(context,spec,cfg)
    record=json.loads((spec.output_root/'decisions.jsonl').read_text())
    assert record['status']==('error' if case=='pass_then_s2' else 'rejected')
    assert record['stage_results']['s1']['status']==('passed' if case=='pass_then_s2' else 'rejected')
    assert record['diagnostic_errors'] and record['deletion_eligible'] is False
    # Verify durable reload and B04 preserve the diagnostic-failure retention policy.
    from stage_b_mcap_v4.reporting import read_quality_batch
    root,summary,records=read_quality_batch(judged.manifest_path)
    loaded=load_quality_batch(judged.manifest_path)
    delivered=McapDeliveryOperator().run(loaded,mode='run')
    assert delivered.exit_code==1 and delivered.summary['deleted_files']==0
    assert delivered.summary['exported_files']==0 and source.read_bytes()==original
    assert summary['failed_episodes']==1
    assert summary['kept_files']+summary['rejected_files']+summary['error_files']==1


def test_mixed_counts_and_selective_deletion(numerical_engine, monkeypatch, tmp_path):
    spec,cfg,adapter,*_=numerical_engine
    sources=[tmp_path/f'item{i}.mcap' for i in range(4)]
    originals={p:write_marvin_mcap(p) for p in sources}
    normalized=operators.McapReadOperator().run_batch(refs(sources),spec,cfg,adapter)
    context=operators.GlobalStatsOperator().run(normalized,spec,cfg)
    current=[-1]
    stages=sys.modules
    s1=stages['robot_data_processing.stages.stage1_sudden_change']; base1=s1.run_stage1
    def first(*a,**kw):
        current[0]+=1;r=base1(*a,**kw)
        if current[0] in (1,3):r.abnormal_frames[20]=True;r.flagged_count=1
        return r
    monkeypatch.setattr(s1,'run_stage1',first)
    s2=stages['robot_data_processing.stages.stage2_trend_alignment'];base2=s2.run_stage2_on_fields
    def second(*a,**kw):
        if current[0]==2:raise RuntimeError('no quality verdict yet')
        return base2(*a,**kw)
    monkeypatch.setattr(s2,'run_stage2_on_fields',second)
    s4=stages['robot_data_processing.stages.stage4_static_interval'];base4=s4.run_stage4
    def fourth(*a,**kw):
        if current[0]==1:raise RuntimeError('known bad followed by diagnostic error')
        return base4(*a,**kw)
    monkeypatch.setattr(s4,'run_stage4',fourth)
    judged=operators.QualityDetectionOperator().run_batch(context,spec,cfg)
    delivered=McapDeliveryOperator().run(load_quality_batch(judged.manifest_path),mode='run')
    s=delivered.summary
    assert (s['kept_files'],s['rejected_files'],s['error_files'],s['failed_episodes'])==(1,2,1,2)
    assert s['exported_files']==1 and s['deleted_files']==1 and delivered.exit_code==1
    assert all(p.read_bytes()==originals[p] for p in sources[:3]) and not sources[3].exists()

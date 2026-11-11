import assert from 'node:assert/strict';
import test from 'node:test';
import { documentResults, sessionLifecycle } from '../src/reviewSession.ts';
import type { Project, Lifecycle } from '../src/types.ts';

const project={
  findings:[{id:'old',document_id:'requirements',cycle_id:'old-cycle'},
            {id:'new',document_id:'requirements',cycle_id:'new-cycle'},
            {id:'other',document_id:'design',cycle_id:'design-cycle'}],
  runs:[{id:'old-run',document_id:'requirements',cycle_id:'old-cycle',events:[{kind:'stage',time:1}]},
        {id:'new-run',document_id:'requirements',cycle_id:'new-cycle',events:[{kind:'stage',time:2}]},
        {id:'other-run',document_id:'design',cycle_id:'design-cycle',events:[]}],
} as Project;

test('opening an existing document before starting shows no current opinions',()=>{
  const value=documentResults(project,'requirements','');
  assert.equal(value.currentFindings.length,0);
  assert.deepEqual(value.historyFindings.map(f=>f.id),['old','new']);
  assert.equal(value.historyRuns.length,2);
});
test('a new review only displays opinions from its real cycle',()=>{
  const value=documentResults(project,'requirements','new-cycle');
  assert.deepEqual(value.currentFindings.map(f=>f.id),['new']);
  assert.deepEqual(value.historyFindings.map(f=>f.id),['old']);
  assert.deepEqual(value.historyEvents.map(e=>e.time),[1]);
});
test('changing document cannot inherit another document opinions',()=>{
  assert.equal(documentResults(project,'design','new-cycle').currentFindings.length,0);
  assert.deepEqual(documentResults(project,'design','').historyFindings.map(f=>f.id),['other']);
});
const life={runs:project.runs,selected_run:'old-run',trace:{run_id:'old-run'},
  consumption_graph:{nodes:[{id:'old-input'}],edges:[]}} as Lifecycle;
test('an API-selected historical trace does not appear in an unstarted session',()=>{
  const value=sessionLifecycle(life,'',false)!;
  assert.equal(value.trace,null);
  assert.equal(value.selected_run,null);
  assert.equal(value.runs.length,0);
  assert.equal(value.consumption_graph.nodes.length,0);
});
test('history is accessible explicitly without mutating the original records',()=>{
  assert.equal(sessionLifecycle(life,'',true)!.trace?.run_id,'old-run');
  assert.equal(life.consumption_graph.nodes.length,1);
  assert.equal(life.runs.length,3);
});
test('old traces cannot leak into a running new cycle',()=>{
  const value=sessionLifecycle(life,'new-cycle',false)!;
  assert.deepEqual(value.runs.map(r=>r.id),['new-run']);
  assert.equal(value.trace,null);
});

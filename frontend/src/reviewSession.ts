import type { Lifecycle, Project } from './types';

export function documentResults(project: Pick<Project, 'findings' | 'runs'> | null, documentId: string, cycleId: string) {
  const findings=project?.findings.filter(f=>f.document_id===documentId)||[];
  const runs=project?.runs.filter(r=>r.document_id===documentId)||[];
  const historyRuns=runs.filter(r=>!cycleId||r.cycle_id!==cycleId);
  return {
    currentFindings:findings.filter(f=>!!cycleId&&f.cycle_id===cycleId),
    historyFindings:findings.filter(f=>!cycleId||f.cycle_id!==cycleId),
    historyRuns,
    historyEvents:historyRuns.flatMap(r=>r.events).sort((a,b)=>(a.time||0)-(b.time||0)),
  };
}

export function sessionLifecycle(life: Lifecycle | null, cycleId: string, historical: boolean): Lifecycle | null {
  if(!life)return null;
  const runs=life.runs.filter(r=>historical?(!cycleId||r.cycle_id!==cycleId):!!cycleId&&r.cycle_id===cycleId);
  const selected=runs.some(r=>r.id===life.selected_run)?life.selected_run:null;
  return {...life,runs,selected_run:selected,trace:selected?life.trace:null,
    consumption_graph:selected?life.consumption_graph:{nodes:[],edges:[]}};
}

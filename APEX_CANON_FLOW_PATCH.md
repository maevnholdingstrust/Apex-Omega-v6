APEX-OMEGA CANON FLOW PATCH  REQUIRED

Patch the pipeline so the execution sequence is strictly:

gate
 C1
 fork sim
 execute C1
 reload state
 C2
 fork sim
 execute/no-op

MANDATORY RULES:
1. C2 must NOT approve, block, or pre-filter C1.
2. C2 only runs after C1 execution completes or after C1 state mutation is simulated/confirmed.
3. C2 must consume reloaded post-C1 state, not pre-C1 state.
4. Punch 2 is a new recompute from the new state.
5. C2 output is only:
   - EXECUTE
   - NO_OP
6. Execution layer remains mechanical only.
7. Fork simulation must happen separately:
   - once before C1 execution
   - once before C2 execution/no-op approval
8. Existing pre_execution_pipeline must be renamed or rewritten if it currently does:
   gate  C1  C2  fork  execute
9. Add tests proving:
   - C2 is not called before C1 fork sim
   - C2 is not called before C1 execution/reload
   - reload_state is called between C1 and C2
   - C2 receives post-C1 state
   - C2 can return NO_OP without failure
   - C1 fork sim and C2 fork sim are separate validations

Return:
- files changed
- exact flow enforced
- tests added
- any unresolved blockers

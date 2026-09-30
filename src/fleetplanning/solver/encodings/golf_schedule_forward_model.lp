% Continuous forward-planning model (notebook: "Forward planning with service history",
% golf_schedule_model.lp). Copied from the notebook, not rewritten.
%
% Difference from golf_schedule_wrap_around_model.lp: the horizon is NOT circular. The
% first task of each area is constrained against the service *history* (last/4) and the
% last task against the planning-horizon end (latest feasible start). The wrap-around
% boundary rules are kept commented out below, exactly as in the notebook.

% for every area, choose the number of starts within bounds
{ num_starts(A,N) : N = Min..Max } = 1 :- area(A), min_starts(A,Min), max_starts(A,Max).

% define the tasks for each area
task(A,1..M) :- area(A), num_starts(A,M).

% each task gets assigned a capable mower
{ asg(A,T,M) : capable(M,A) } = 1 :- task(A,T).

% every tasks gets a start time
&dom { S : completion(A,M,S,_,_) } = start(A,T) :- asg(A,T,M).
start_val(A,T,S) :- &sum{start(A,T)} = S, asg(A,T,M), completion(A,M,S,_,_).
start_info(A,T,S,AH) :- asg(A,T,M), start_val(A,T,S), completion(A,M,S,_,AH).

% every tasks gets a completion time
&dom { C : completion(A,M,S,C,AH) } = completion(A,T) :- asg(A,T,M), start_val(A,T,S).

% enforce order between consecutive tasks of the same area
:- task(A,N), task(A,N+1), &sum { start(A,N+1)} < completion(A,N).

% no two tasks assigned to the same mower can overlap
:- asg(A1,N1,M), asg(A2,N2,M), (A1,N1) < (A2,N2),
   &sum { start(A1,N1) } < completion(A2,N2),
   &sum { start(A2,N2) } < completion(A1,N1).

% no task can overlap with the last task with the same mower
:- last(A,M,S,C), asg(A,T,M), &sum { start(A,T) } < C.


% violations of service constraints
max_interval_violation(A,T) :- task(A,T), task(A,T+1), max_interval(A,I),
         &sum{ start(A,T+1) - start(A,T)} > I.

min_interval_violation(A,T) :- task(A,T), task(A,T+1), min_interval(A,I),
         &sum{ start(A,T+1) - start(A,T)} < I.


% wrapping around violations of service constraints for the first and last task of each area
%last_task(A,T) :-  task(A,T), not task(A,T+1).
%max_interval_violation(A,T) :- last_task(A,T), max_interval(A,I),
%            &sum{ (24*7); -start(A,T); start(A,1)} > I.
%min_interval_violation(A,T) :- last_task(A,T), min_interval(A,I),
%            &sum{ (24*7); -start(A,T); start(A,1)} < I.


% violations of start time constraints for the first and last task of each area
first_task_too_late(A) :- task(A,1), max_interval(A,I),
                          last(A,_,S,_), &sum { start(A,1) } > N, N = S + I.

first_task_too_early(A) :- task(A,1), min_interval(A,I),
                          last(A,_,S,_), &sum { start(A,1) } < N, N = S + I.

last_task(A,T) :-  task(A,T), not task(A,T+1).
latest_start(A,L) :- area(A), L = #max { S : completion(A,M,S,_,_) }.
last_task_too_early(A) :- last_task(A,T), max_interval(A,I),
                          latest_start(A,Latest_Start),
                          &sum { start(A,T) } < Latest_Start - I.

% priority must be 1..3 (ADR-0012): the level 5-P below must stay in {4,3,2}, strictly
% above the avoid-zone level (@1) and the min-interval level (@0). P >= 4 would collide.
:- priority(_,P), P < 1.
:- priority(_,P), P > 3.

% optimize the schedule by minimizing the total violation
%
% max-interval grading (ADR-0055): layered, not flat. Layer K is violated once the
% overshoot exceeds K full max_interval-sized steps, so a gap/boundary that is only
% just over the limit still costs 1 (layer 1 fires under the exact same condition as
% the flat rule it replaces, verified: gap_over(A,T,1) is "> I", identical to
% max_interval_violation's own "> I"; likewise for the other two), while one that is
% e.g. 3x over costs 3 instead of the same flat 1 — no more free ride once a gap is
% already violated. mi_layer/2's second rule guarantees layer 1 always exists even if
% the natural bound N0 computes below 1 (a very short horizon/history relative to
% max_interval) — belt-and-braces so today's coverage is never silently lost.
mi_layer(A,1..N0) :- max_interval(A,I), latest_start(A,L), last(A,_,S,_),
                     N0 = (L-S-I)/I+1, N0 >= 1.
mi_layer(A,1) :- area(A).

gap_over(A,T,K) :- task(A,T), task(A,T+1), max_interval(A,I), mi_layer(A,K),
                   &sum{ start(A,T+1) - start(A,T) } > K*I.

first_over(A,K) :- task(A,1), max_interval(A,I), mi_layer(A,K), last(A,_,S,_),
                   &sum { start(A,1) } > S+K*I.

last_under(A,K) :- last_task(A,T), max_interval(A,I), mi_layer(A,K), latest_start(A,L),
                   &sum { start(A,T) } < L-K*I.

% first_over/2 and last_under/2 carry a trailing marker (`first`/`last`) so their weak-
% constraint elements can never alias each other's: without it, an area whose first task
% is too late AND whose last task is too early at the same layer K produced the identical
% element `1@5-P, A, K` from both rules, and ASP's weak-constraint element-SET semantics
% collapsed the two into one instead of summing them (ADR-0056). gap_over/3 never had this
% problem — its extra T term already makes every element distinct.
:~ gap_over(A,T,K), priority(A,P). [1@5-P, A, T, K]
:~ first_over(A,K), priority(A,P). [1@5-P, A, K, first]
:~ last_under(A,K), priority(A,P). [1@5-P, A, K, last]

avoid_zone_violation(A,T) :- start_info(A,T,_,AH), AH > 0.
:~ avoid_zone_violation(A,T). [1@1, A, T]

:~ min_interval_violation(A,T), priority(A,P). [1@0, A, T]
:~ first_task_too_early(A), priority(A,P). [1@0, A]


% show statements
#show.
#show schedule(A,T,M,S,C) : asg(A,T,M), start_val(A,T,S), completion(A,M,S,C,AH).
#show max_interval_violation/2.
#show min_interval_violation/2.
#show avoid_zone_violation/2.
#show first_task_too_late/1.
#show first_task_too_early/1.
#show last_task_too_early/1.

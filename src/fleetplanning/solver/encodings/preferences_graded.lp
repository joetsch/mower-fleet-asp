% The preference layer, graded variant (ADR-0033 arm `weak-graded`), settling ADR-0031
% decision 4. Same shape as preferences_weak.lp; only the time half is graded, because the
% mower half has no metric. `pref_maxdev` is what an area with no task at all costs; the
% runner sets it to the scenario horizon.
#const pref_level=6.
#const pref_maxdev=24.

pref_time_met(A,S)    :- pref_time(A,S), start_val(A,_,S).
pref_mower_met(A,S,M) :- pref_mower(A,S,M), asg(A,T,M), start_val(A,T,S).

% How far the nearest task of A ended up from the preferred hour, when none is on it.
pref_dev(A,S,D) :- pref_time(A,S), not pref_time_met(A,S),
                   start_val(A,_,S2), D = |S2 - S|.
pref_cost(A,S,D) :- pref_time(A,S), not pref_time_met(A,S),
                    D = #min{ D2 : pref_dev(A,S,D2); pref_maxdev }.

:~ pref_cost(A,S,D).                             [D@pref_level, A, S]
:~ pref_mower(A,S,M), not pref_mower_met(A,S,M). [1@pref_level, A, S, M]

% The preference layer (ADR-0031/0032). Consumes pref_time/2 and pref_mower/3, emitted by
% solver/preferences.py. Reuses start_val/3 and asg/3 from the forward model; adds nothing
% to it. `pref_level` is set from the command line: 6 = above every service-quality level,
% -1 = a tie-break below all of them.
#const pref_level=6.

% A preference is met when *some* task of that area is there — no task index appears.
pref_time_met(A,S)    :- pref_time(A,S), start_val(A,_,S).
pref_mower_met(A,S,M) :- pref_mower(A,S,M), asg(A,T,M), start_val(A,T,S).

:~ pref_time(A,S),    not pref_time_met(A,S).    [1@pref_level, A, S]
:~ pref_mower(A,S,M), not pref_mower_met(A,S,M). [1@pref_level, A, S, M]

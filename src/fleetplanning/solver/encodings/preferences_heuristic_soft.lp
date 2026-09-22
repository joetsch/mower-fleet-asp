% The preference layer, mechanism B, soft-modifier variant (ADR-0033 arm `heur-soft`).
% The same two directives as preferences_heuristic.lp on the other end of the AAAI-13
% ladder: `init` biases only the initial decision score rather than forcing the variable
% order, `sign` fixes the preferred truth value. Requires --heuristic=Domain.
#const pref_w=10.

#heuristic start_val(A,T,S) : pref_time(A,S), task(A,T).           [pref_w,init]
#heuristic start_val(A,T,S) : pref_time(A,S), task(A,T).           [1,sign]
#heuristic asg(A,T,M)       : pref_mower(A,S,M), start_val(A,T,S). [pref_w,init]
#heuristic asg(A,T,M)       : pref_mower(A,S,M), start_val(A,T,S). [1,sign]

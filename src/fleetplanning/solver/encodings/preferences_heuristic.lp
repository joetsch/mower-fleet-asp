% The preference layer, mechanism B. Adds no weak constraint and no choice atom: the
% optimisation problem is exactly the forward model's. Requires --heuristic=Domain on the
% command line, or clingo ignores these directives entirely.
#const pref_w=10.

#heuristic start_val(A,T,S) : pref_time(A,S), task(A,T).           [pref_w,true]
#heuristic asg(A,T,M)       : pref_mower(A,S,M), start_val(A,T,S). [pref_w,true]

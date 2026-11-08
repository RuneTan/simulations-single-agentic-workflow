"""Single-Agent task workflow: state, model client, tools and LangGraph stages.

The package implements the minimal delivery described in
``docs/REVIEW.md``: one task Agent drives a MadGraph
generation task while the five accepted checkers run automatically at stage
handoffs.  Large files stay in the task workspace; the graph carries paths,
hashes and check results.
"""

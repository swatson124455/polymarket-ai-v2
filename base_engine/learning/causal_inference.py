"""
Causal Inference & Counterfactual Analysis — real implementation using causal-learn.

Features:
- PC algorithm for causal graph learning via causal-learn library
- do-calculus based intervention analysis
- Counterfactual "what-if" scenarios
- Causal feature importance (direct + indirect paths)

Dependencies: causal-learn (pip install causal-learn)
"""
import numpy as np
from typing import Dict, List, Optional, Any, Tuple
from structlog import get_logger

logger = get_logger()


class CausalInferenceEngine:
    """
    Causal inference and counterfactual analysis for prediction markets.

    Uses the PC algorithm (Peter-Clark) for constraint-based causal discovery
    to learn DAGs from observational data, then performs do-calculus
    intervention analysis and causal importance computation.
    """

    def __init__(self):
        self.causal_graphs: Dict[str, Dict[str, Any]] = {}
        self.intervention_history: List[Dict[str, Any]] = []
        self._pc_available = self._check_pc_available()

    def _check_pc_available(self) -> bool:
        try:
            from causallearn.search.ConstraintBased.PC import pc
            return True
        except ImportError:
            logger.info("causal-learn not installed — causal inference uses fallback heuristics")
            return False

    async def learn_causal_graph(
        self,
        market_id: str,
        features: List[str],
        outcomes: List[str],
        data: Optional[np.ndarray] = None,
    ) -> Dict[str, Any]:
        """
        Learn causal graph using the PC algorithm.

        Args:
            market_id: Market identifier.
            features: List of feature names.
            outcomes: List of outcome variable names.
            data: Optional N x D numpy array of observations.

        Returns:
            Causal graph with nodes, edges, and adjacency matrix.
        """
        all_vars = features + outcomes
        logger.info("Learning causal graph for %s with %d variables", market_id, len(all_vars))

        if data is not None and self._pc_available and data.shape[0] >= 20:
            try:
                from causallearn.search.ConstraintBased.PC import pc
                cg = pc(data, alpha=0.05, indep_test="fisherz")

                # Extract adjacency matrix and edges
                adj = cg.G.graph  # numpy array
                edges = []
                for i in range(len(all_vars)):
                    for j in range(i + 1, len(all_vars)):
                        if adj[i, j] != 0 or adj[j, i] != 0:
                            if adj[i, j] == -1 and adj[j, i] == 1:
                                edges.append({"from": all_vars[i], "to": all_vars[j], "type": "directed"})
                            elif adj[i, j] == 1 and adj[j, i] == -1:
                                edges.append({"from": all_vars[j], "to": all_vars[i], "type": "directed"})
                            else:
                                edges.append({"from": all_vars[i], "to": all_vars[j], "type": "undirected"})

                graph = {
                    "market_id": market_id,
                    "nodes": all_vars,
                    "edges": edges,
                    "adjacency": adj.tolist(),
                    "algorithm": "PC",
                    "n_samples": data.shape[0],
                }
                self.causal_graphs[market_id] = graph
                logger.info("PC algorithm learned %d edges for %s", len(edges), market_id)
                return graph

            except Exception as e:
                logger.warning("PC algorithm failed for %s: %s — falling back", market_id, e)

        # Fallback: correlation-based heuristic graph
        graph = self._learn_heuristic_graph(market_id, all_vars, data)
        self.causal_graphs[market_id] = graph
        return graph

    def _learn_heuristic_graph(
        self, market_id: str, variables: List[str], data: Optional[np.ndarray]
    ) -> Dict[str, Any]:
        """Fallback: learn graph from correlations when PC unavailable."""
        edges = []
        if data is not None and data.shape[0] >= 5 and data.shape[1] >= 2:
            try:
                corr = np.corrcoef(data.T)
                for i in range(len(variables)):
                    for j in range(i + 1, len(variables)):
                        if abs(corr[i, j]) > 0.3:
                            edges.append({
                                "from": variables[i], "to": variables[j],
                                "type": "undirected", "correlation": float(corr[i, j]),
                            })
            except Exception as _e:
                # L5 FIX: Replace bare except:pass — causal graph computation failed silently.
                logger.info("Causal inference edge computation failed (using partial results): %s", _e)
        return {
            "market_id": market_id,
            "nodes": variables,
            "edges": edges,
            "algorithm": "correlation_heuristic",
        }

    async def analyze_counterfactual(
        self,
        market_id: str,
        intervention: Dict[str, float],
        baseline: Dict[str, float],
    ) -> Dict[str, Any]:
        """
        Counterfactual analysis using do-calculus.

        Estimates: P(outcome | do(X=x)) vs P(outcome | X=x_baseline).

        Args:
            market_id: Market ID.
            intervention: Variables and their counterfactual values.
            baseline: Current/baseline values.

        Returns:
            Counterfactual effect estimate.
        """
        graph = self.causal_graphs.get(market_id)
        if not graph:
            return {
                "market_id": market_id, "intervention": intervention,
                "effect": 0.0, "confidence": 0.1,
                "note": "No causal graph available — learn one first",
            }

        # do-calculus: for each intervened variable, trace directed paths to outcomes
        edges = graph.get("edges", [])
        nodes = graph.get("nodes", [])

        total_effect = 0.0
        paths_found = 0
        for var, new_val in intervention.items():
            old_val = baseline.get(var, 0.0)
            delta = new_val - old_val
            if abs(delta) < 1e-10:
                continue
            # Find direct causal paths from this variable to any outcome
            reachable = self._find_reachable(var, edges, nodes)
            for target in reachable:
                if target not in intervention:
                    # Simple linear approximation of causal effect
                    # In full implementation: use structural equations
                    path_strength = self._path_strength(var, target, edges)
                    effect = delta * path_strength
                    total_effect += effect
                    paths_found += 1

        confidence = min(0.9, 0.3 + paths_found * 0.1)
        result = {
            "market_id": market_id,
            "intervention": intervention,
            "baseline": baseline,
            "predicted_effect": total_effect,
            "confidence": confidence,
            "paths_found": paths_found,
        }
        self.intervention_history.append(result)
        return result

    async def analyze_intervention(
        self,
        market_id: str,
        variable: str,
        intervention_value: float,
    ) -> Dict[str, Any]:
        """
        Analyze the effect of do(variable = intervention_value).

        Uses the causal graph to estimate downstream effects.
        """
        graph = self.causal_graphs.get(market_id)
        if not graph:
            return {
                "market_id": market_id, "variable": variable,
                "predicted_effect": 0.0, "confidence": 0.1,
            }

        edges = graph.get("edges", [])
        nodes = graph.get("nodes", [])
        reachable = self._find_reachable(variable, edges, nodes)

        downstream_effects = {}
        for target in reachable:
            strength = self._path_strength(variable, target, edges)
            downstream_effects[target] = intervention_value * strength

        return {
            "market_id": market_id,
            "variable": variable,
            "intervention_value": intervention_value,
            "downstream_effects": downstream_effects,
            "predicted_effect": sum(downstream_effects.values()) if downstream_effects else 0.0,
            "confidence": min(0.8, 0.2 + len(downstream_effects) * 0.1),
        }

    def get_causal_importance(
        self,
        market_id: str,
        outcome: str,
    ) -> Dict[str, float]:
        """
        Compute causal importance of each feature for the outcome.

        Combines direct causal path strength + indirect path contributions.
        """
        graph = self.causal_graphs.get(market_id)
        if not graph:
            return {}

        edges = graph.get("edges", [])
        nodes = graph.get("nodes", [])
        importance = {}

        for node in nodes:
            if node == outcome:
                continue
            # Direct path strength
            direct = self._path_strength(node, outcome, edges)
            # Indirect: paths through intermediaries
            indirect = 0.0
            reachable = self._find_reachable(node, edges, nodes)
            for intermediate in reachable:
                if intermediate != outcome and intermediate != node:
                    step1 = self._path_strength(node, intermediate, edges)
                    step2 = self._path_strength(intermediate, outcome, edges)
                    indirect += step1 * step2
            total = direct + indirect * 0.5  # Discount indirect paths
            if total > 0:
                importance[node] = min(1.0, total)

        # Normalize to sum to 1.0
        total_imp = sum(importance.values())
        if total_imp > 0:
            importance = {k: v / total_imp for k, v in importance.items()}

        return importance

    def get_causal_graph(self, market_id: str) -> Optional[Dict[str, Any]]:
        """Get the learned causal graph for a market."""
        return self.causal_graphs.get(market_id)

    # ── Helpers ──────────────────────────────────────────────────────────

    def _find_reachable(self, source: str, edges: List[Dict], nodes: List[str]) -> List[str]:
        """Find all nodes reachable from source via directed edges (BFS)."""
        reachable = set()
        frontier = [source]
        while frontier:
            current = frontier.pop(0)
            for edge in edges:
                if edge.get("from") == current and edge.get("type") == "directed":
                    target = edge["to"]
                    if target not in reachable and target != source:
                        reachable.add(target)
                        frontier.append(target)
        return list(reachable)

    def _path_strength(self, source: str, target: str, edges: List[Dict]) -> float:
        """Estimate edge strength between two nodes (0 if no direct edge)."""
        for edge in edges:
            if edge.get("from") == source and edge.get("to") == target:
                return abs(edge.get("correlation", 0.5))
            # Undirected edge: half strength
            if edge.get("type") == "undirected":
                if (edge.get("from") == source and edge.get("to") == target) or \
                   (edge.get("from") == target and edge.get("to") == source):
                    return abs(edge.get("correlation", 0.3)) * 0.5
        return 0.0

/**
 * Flowchart Module
 *
 * Renders a top-to-bottom process flow as styled divs with CSS connectors —
 * no diagramming library, so no third-party parser handles model output.
 *
 * Node labels and IDs come from the LLM. The server drops dangling edges,
 * self-loops and duplicates before this runs; this module additionally caps
 * the node count and renders every label through SafeDOM as text.
 */

(function () {
    'use strict';

    const S = window.SafeDOM;

    // Matches the server-side cap. A runaway graph would otherwise be a
    // client-side denial of service on a slow device.
    const MAX_RENDERED_NODES = 40;

    function initFlowchart(data) {
        const canvas = document.getElementById('flowchart-canvas');
        const title = document.getElementById('flowchart-title');
        if (!canvas) return;

        S.clear(canvas);

        const nodes = (data && Array.isArray(data.nodes) ? data.nodes : []).filter(function (n) {
            return n && typeof n.id === 'string' && n.id && typeof n.label === 'string' && n.label.trim();
        });

        if (!nodes.length) {
            canvas.appendChild(
                S.el('div', 'flowchart-empty', 'No flowchart data available for this lecture.')
            );
            if (title) title.textContent = '';
            return;
        }

        if (title) title.textContent = S.str(data.title, 200) || 'PROCESS FLOW';

        const edges = (data && Array.isArray(data.edges) ? data.edges : []).filter(function (e) {
            return e && typeof e.source === 'string' && typeof e.target === 'string';
        });

        const ordered = linearize(nodes, buildAdjacency(edges)).slice(0, MAX_RENDERED_NODES);

        ordered.forEach(function (node, i) {
            const el = S.el('div', 'flow-node');
            S.append(el, [
                S.el('div', 'flow-node__num', S.pad(i + 1, 2)),
                S.el('div', 'flow-node__label', S.str(node.label, 300)),
            ]);
            canvas.appendChild(el);

            if (i < ordered.length - 1) {
                const arrow = S.el('div', 'flow-arrow');
                arrow.setAttribute('aria-hidden', 'true');
                S.append(arrow, [
                    S.el('div', 'flow-arrow__line'),
                    S.el('div', 'flow-arrow__head', '▼'),
                ]);
                canvas.appendChild(arrow);
            }
        });
    }

    /**
     * Build source → targets.
     *
     * A Map rather than an object literal: node IDs are model-supplied, and
     * `__proto__` or `constructor` as a key would otherwise touch the
     * prototype chain instead of creating an entry.
     */
    function buildAdjacency(edges) {
        const adjacency = new Map();
        edges.forEach(function (e) {
            if (!adjacency.has(e.source)) adjacency.set(e.source, []);
            adjacency.get(e.source).push(e.target);
        });
        return adjacency;
    }

    /**
     * Order nodes by walking the edge chain from a node with no inbound edge.
     *
     * Falls back to the original order for anything that is not a simple chain,
     * and appends unvisited nodes at the end so nothing is silently dropped.
     * The visited set also bounds the walk, so a cycle cannot loop forever.
     */
    function linearize(nodes, adjacency) {
        if (nodes.length <= 1) return nodes;

        const byId = new Map();
        nodes.forEach(function (n) {
            if (!byId.has(n.id)) byId.set(n.id, n);
        });

        const targets = new Set();
        adjacency.forEach(function (list) {
            list.forEach(function (t) { targets.add(t); });
        });

        let current = nodes.find(function (n) { return !targets.has(n.id); }) || nodes[0];

        const visited = new Set();
        const ordered = [];

        while (current && !visited.has(current.id)) {
            visited.add(current.id);
            ordered.push(current);

            const next = (adjacency.get(current.id) || []).find(function (id) {
                return byId.has(id) && !visited.has(id);
            });
            current = next ? byId.get(next) : null;
        }

        nodes.forEach(function (n) {
            if (!visited.has(n.id)) {
                visited.add(n.id);
                ordered.push(n);
            }
        });

        return ordered;
    }

    window.initFlowchart = initFlowchart;
})();

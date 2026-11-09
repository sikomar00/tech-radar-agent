// 그래프 화면(IA-5)과 기술 상세의 이웃 그래프(IA-3). 데이터는 같은 서버의 /graph.json에서 받는다.
// 이름·설명은 Cytoscape의 글자 데이터와 textContent로만 넣는다(외부 글이 HTML이 되지 않게).
(function () {
  "use strict";
  var TYPES = {
    PRODUCT: ["#1a73e8", "제품"], FRAMEWORK_LIBRARY: ["#188038", "프레임워크·라이브러리"],
    PROTOCOL_STANDARD: ["#e37400", "프로토콜·표준"], MODEL: ["#a142f4", "모델"]
  };
  var NO_TYPE = ["#9aa0a6", "분류 보류"];

  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function byId(id) { return document.getElementById(id); }

  function elements(data) {
    var nodes = data.nodes.map(function (n) {
      return { data: { id: String(n.id), name: n.name, type: n.type || "", what: n.what || "",
                       size: 16 + Math.min(Math.sqrt(n.sources) * 8, 40) } };
    });
    var edges = data.edges.map(function (e) {
      return { data: { id: "e" + e.id, source: String(e.source), target: String(e.target), type: e.type,
                       label: e.label, verified: e.verified, symmetric: e.symmetric } };
    });
    return nodes.concat(edges);
  }

  function style() {
    var fg = css("--fg"), line = css("--muted"), accent = css("--accent");
    return [
      { selector: "node", style: {
        "background-color": function (n) { return (TYPES[n.data("type")] || NO_TYPE)[0]; },
        width: "data(size)", height: "data(size)", label: "data(name)", color: fg, "font-size": 11,
        "text-valign": "bottom", "text-margin-y": 4 } },
      { selector: "edge", style: {
        width: 1.5, "line-color": line, "target-arrow-color": line, "target-arrow-shape": "triangle",
        "curve-style": "bezier", label: "data(label)", "font-size": 9, color: line, "text-rotation": "autorotate" } },
      { selector: "edge[?symmetric]", style: { "target-arrow-shape": "none" } },
      { selector: "edge[!verified]", style: { "line-style": "dashed" } },
      { selector: ".center", style: { "border-width": 3, "border-color": fg } },
      { selector: ".found", style: { "border-width": 4, "border-color": accent } },
      { selector: ".faded", style: { opacity: 0.15 } }
    ];
  }

  function draw(container, url) {
    return fetch(url, { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) { throw new Error("HTTP " + r.status); } return r.json(); })
      .then(function (data) {
        var cy = cytoscape({ container: container, elements: elements(data), style: style(),
                             layout: { name: "cose", animate: false } });
        cy.on("tap", "node", function (evt) { window.location.href = "/kb/" + encodeURIComponent(evt.target.id()); });
        cy.on("mouseover", "node", function (evt) {
          var n = evt.target, text = n.data("name") + (n.data("what") ? " — " + n.data("what") : "");
          container.title = text;
          if (byId("graph-status")) { byId("graph-status").textContent = text; }
        });
        return cy;
      })
      .catch(function (err) { container.textContent = "그래프를 불러오지 못했습니다(" + err.message + "). 아래 표를 보세요."; });
  }

  function legend() {
    var box = byId("graph-legend");
    if (!box) { return; }
    Object.keys(TYPES).map(function (k) { return TYPES[k]; }).concat([NO_TYPE]).forEach(function (t) {
      var item = document.createElement("span"), dot = document.createElement("span");
      dot.style.cssText = "display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px;background:" + t[0];
      item.appendChild(dot);
      item.appendChild(document.createTextNode(t[1]));
      box.appendChild(item);
    });
  }

  function filter(cy) {
    var type = byId("f-type").value, relation = byId("f-relation").value, verifiedOnly = byId("f-verified").checked;
    cy.batch(function () {
      cy.edges().forEach(function (e) {
        var hide = (relation && e.data("type") !== relation) || (verifiedOnly && !e.data("verified"));
        e.style("display", hide ? "none" : "element");
      });
      cy.nodes().forEach(function (n) { n.toggleClass("faded", Boolean(type) && n.data("type") !== type); });
    });
  }

  function find(cy, name) {
    var wanted = name.trim().toLowerCase();
    cy.nodes().removeClass("found");
    var hit = cy.nodes().filter(function (n) { return n.data("name").toLowerCase() === wanted; });
    if (hit.length) { hit.addClass("found"); cy.animate({ center: { eles: hit }, zoom: 1.5 }, { duration: 300 }); }
  }

  function setupMain() {
    var container = byId("graph");
    if (!container) { return; }
    legend();
    draw(container, container.dataset.graphUrl).then(function (cy) {
      if (!cy) { return; }
      ["f-type", "f-relation", "f-verified"].forEach(function (id) { byId(id).addEventListener("change", function () { filter(cy); }); });
      byId("f-name").addEventListener("change", function (evt) { find(cy, evt.target.value); });
    });
  }

  function setupNeighborhood() {
    var container = byId("neighborhood"), toggle = byId("depth-toggle");
    if (!container) { return; }
    var current = null;
    function load(depth) {
      if (current) { current.destroy(); current = null; }
      toggle.disabled = true;  // 빠르게 두 번 누르면 한 칸에 그래프가 둘 생긴다
      draw(container, container.dataset.graphUrl + "&depth=" + depth).then(function (cy) {
        current = cy || null;
        toggle.disabled = false;
        if (cy) { cy.getElementById(container.dataset.center).addClass("center"); }
      });
    }
    load(1);
    toggle.addEventListener("click", function () {
      var depth = toggle.dataset.depth === "1" ? 2 : 1;
      toggle.dataset.depth = String(depth);
      toggle.textContent = depth === 2 ? "1단계만 보기" : "2단계까지 보기";
      load(depth);
    });
  }

  document.addEventListener("DOMContentLoaded", function () { setupMain(); setupNeighborhood(); });
})();

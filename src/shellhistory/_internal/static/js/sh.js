/* Everything the chart pages share: the filter bar, the fetching, and the
 * turning of one standard payload into a Highcharts chart.
 *
 * A page says what it wants drawn and from where; which commands end up in it
 * is decided here, out of the query string, so that every chart filters and
 * splits the same way and none of them has to know how.
 */
var SH = (function ($) {
  "use strict";

  var params = new URLSearchParams(window.location.search);
  var meta = null;
  var page = null;

  // Shown in the bar itself; everything else waits behind "more filters", or
  // fourteen dropdowns would sit above every chart.
  var PRIMARY = ["host", "env"];

  var PALETTE = [
    "#7cb5ec", "#f7a35c", "#90ed7d", "#e4d354", "#f45b5b",
    "#8085e9", "#91e8e1", "#2b908f", "#e4a3ff", "#434348"
  ];

  function query() {
    var text = params.toString();
    return text ? "?" + text : "";
  }

  function set(key, value) {
    if (value === null || value === undefined || value === "") {
      params.delete(key);
    } else {
      params.set(key, value);
    }
    var text = params.toString();
    window.history.replaceState(null, "", window.location.pathname + (text ? "?" + text : ""));
    reload();
  }

  function status(message) {
    var box = $("#sh-status");
    if (message) { box.text(message).show(); } else { box.hide(); }
  }

  function reload() {
    if (!page) { return; }
    $("#sh-normalize-box").toggle(!!params.get("split"));
    status("Loading…");
    $.getJSON(page.endpoint + query())
      .done(function (data) {
        status(null);
        try {
          page.draw(data);
        } catch (error) {
          status("Could not draw this chart: " + error);
        }
      })
      .fail(function (xhr) {
        status("Could not load this chart (" + xhr.status + " " + xhr.statusText + ").");
      });
  }

  function option(value, label, selected) {
    return $("<option>").attr("value", value).text(label).prop("selected", selected);
  }

  function facetSelect(facet) {
    var select = $("<select>").addClass("form-control input-sm").attr("data-facet", facet.name);
    select.append(option("", "All", !params.get(facet.name)));
    facet.values.forEach(function (value) {
      select.append(option(value, value, params.get(facet.name) === value));
    });
    select.on("change", function () { set(facet.name, this.value); });
    return $("<label>").text(facet.label + " ").append(select);
  }

  function prefixInput(prefix) {
    var input = $("<input>")
      .addClass("form-control input-sm")
      .attr({ type: "text", placeholder: "starts with…" })
      .val(params.get(prefix.name) || "");
    input.on("change", function () { set(prefix.name, this.value.trim()); });
    return $("<label>").text(prefix.label + " ").append(input);
  }

  function buildBar(options) {
    var bar = $("#sh-controls");
    if (!bar.length) { return; }
    var main = $("<div>").addClass("sh-row");
    var extra = $("<div>").addClass("sh-row sh-extra").hide();

    if (options.splittable !== false) {
      var split = $("<select>").addClass("form-control input-sm").attr("id", "sh-split");
      split.append(option("", "nothing", !params.get("split")));
      meta.facets.filter(function (f) { return f.splittable; }).forEach(function (f) {
        split.append(option(f.name, f.label, params.get("split") === f.name));
      });
      split.on("change", function () { set("split", this.value); });
      main.append($("<label>").text("Split by ").append(split));
    }

    meta.facets.forEach(function (facet) {
      (PRIMARY.indexOf(facet.name) >= 0 ? main : extra).append(facetSelect(facet));
    });
    meta.prefixes.forEach(function (prefix) { extra.append(prefixInput(prefix)); });

    ["since", "until"].forEach(function (bound) {
      var input = $("<input>")
        .addClass("form-control input-sm")
        .attr({ type: "date" })
        .val(params.get(bound) || "");
      input.on("change", function () { set(bound, this.value); });
      main.append($("<label>").text(bound === "since" ? "From " : "To ").append(input));
    });

    if (options.granularity) {
      var period = $("<select>").addClass("form-control input-sm");
      meta.granularities.forEach(function (name) {
        period.append(option(name, name, (params.get("granularity") || "month") === name));
      });
      period.on("change", function () { set("granularity", this.value); });
      main.append($("<label>").text("Per ").append(period));
    }

    if (options.splittable !== false) {
      var normalize = $("<input>")
        .attr({ type: "checkbox", id: "sh-normalize" })
        .prop("checked", !!params.get("normalize"));
      normalize.on("change", function () { set("normalize", this.checked ? "1" : ""); });
      main.append(
        $("<label>")
          .attr("id", "sh-normalize-box")
          .attr("title", "Scale every series to its own total, so that a busy machine and a quiet one can be compared")
          .append(normalize)
          .append(" share of each series")
          .toggle(!!params.get("split"))
      );
    }

    if (extra.children().length) {
      var toggle = $("<a>").attr("href", "#").text("more filters");
      toggle.on("click", function (event) {
        event.preventDefault();
        extra.toggle();
        toggle.text(extra.is(":visible") ? "fewer filters" : "more filters");
      });
      main.append(toggle);
    }

    var reset = $("<a>").attr("href", window.location.pathname).text("reset");
    main.append(reset);

    bar.append(main).append(extra);
  }

  /* One payload, one set of Highcharts options. */
  function build(data, spec) {
    spec = spec || {};
    var stacking = spec.stacking === "auto" ? (data.split ? "normal" : null) : (spec.stacking || null);
    var axis = data.xType === "datetime"
      ? { type: "datetime", title: { text: spec.xTitle || null } }
      : { categories: data.categories, title: { text: spec.xTitle || null }, crosshair: true };
    // An explicitly undefined labels object breaks Highcharts' automatic
    // category-label rotation. Omit it so the library can merge its defaults.
    if (spec.xLabels) { axis.labels = spec.xLabels; }
    return {
      chart: { type: spec.type || "column", zoomType: "x" },
      colors: PALETTE,
      title: { text: spec.title || null },
      subtitle: { text: spec.subtitle || null },
      xAxis: axis,
      yAxis: {
        // Charts of a change need room below zero; everything else starts there.
        min: spec.min === undefined ? 0 : spec.min,
        title: { text: data.normalize ? "% of each series" : (spec.yTitle || "Commands") },
        plotLines: spec.min === null ? [{ value: 0, width: 1, color: "#999", zIndex: 3 }] : undefined
      },
      legend: { enabled: !!data.split || !!spec.legend },
      tooltip: {
        shared: data.xType !== "datetime",
        valueSuffix: data.normalize ? " %" : (spec.valueSuffix || ""),
        valueDecimals: data.normalize ? 2 : spec.valueDecimals
      },
      plotOptions: {
        series: { stacking: stacking, marker: { enabled: false }, animation: false },
        column: { borderWidth: 0, pointPadding: 0.05, groupPadding: 0.1 }
      },
      series: (data.series || []).map(function (entry) {
        return { name: entry.name, data: entry.data };
      })
    };
  }

  function draw(container, data, spec) {
    return Highcharts.chart(container, build(data, spec));
  }

  /* The numbers a distribution is worth reading with, for the subtitle. */
  function summarize(data, unit) {
    if (!data.summary) { return null; }
    var names = Object.keys(data.summary);
    if (!names.length) { return null; }
    unit = unit || "";
    return names.slice(0, 4).map(function (name) {
      var one = data.summary[name];
      return (names.length > 1 ? name + ": " : "") +
        "median " + one.median + unit + ", mean " + one.mean + unit + ", 95% under " + one.p95 + unit;
    }).join(" — ");
  }

  /* Categories carrying a second number are clearer with it spelled out. */
  function annotate(data, key, format) {
    if (!data[key] || !data.categories) { return data; }
    data.categories = data.categories.map(function (name, index) {
      var extra = data[key][index];
      return extra || extra === 0 ? format(name, extra) : name;
    });
    return data;
  }

  /* Empty databases and over-narrow filters are the same problem to a reader. */
  function empty(data) {
    return !data || !data.series || !data.series.length ||
      data.series.every(function (entry) { return !entry.total; });
  }

  function note(container, message) {
    $("#" + container).html($("<p>").addClass("sh-empty").text(message));
  }

  function start(endpoint, drawer, options) {
    options = options || {};
    page = { endpoint: endpoint, draw: drawer };
    $.getJSON("/facets_json")
      .done(function (data) { meta = data; buildBar(options); reload(); })
      .fail(function () { meta = { facets: [], prefixes: [], granularities: [] }; buildBar(options); reload(); });
  }

  return {
    start: start,
    build: build,
    draw: draw,
    summarize: summarize,
    annotate: annotate,
    empty: empty,
    note: note,
    params: params,
    set: set,
    palette: PALETTE
  };
})(jQuery);

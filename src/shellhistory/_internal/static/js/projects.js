/* Which project was being worked on when. */
$(function () {
  var page = window.CHART;
  SH.start(page.endpoint, function (data) {
    if (SH.empty(data.timeline)) {
      SH.note("container", "Nothing recorded for this selection.");
      $("#totals").empty();
      return;
    }
    var options = SH.build(data.timeline, {
      type: "area",
      stacking: "normal",
      title: page.spec.title,
      yTitle: "Commands"
    });
    options.legend.enabled = true;
    Highcharts.chart("container", options);

    Highcharts.chart("totals", {
      chart: { type: "bar", height: 420 },
      palette: { colors: SH.palette },
      title: { text: "In total" },
      xAxis: { categories: data.totals.map(function (entry) { return entry.name; }) },
      yAxis: { min: 0, title: { text: "Commands" } },
      legend: { enabled: false },
      plotOptions: { series: { animation: false } },
      series: [{ name: "Commands", data: data.totals.map(function (entry) { return entry.y; }) }]
    });
  }, page);
});

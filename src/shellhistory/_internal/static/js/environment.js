/* Where the commands were typed: the bands over time, and the totals beside them. */
$(function () {
  var page = window.CHART;
  SH.start(page.endpoint, function (data) {
    if (SH.empty(data.timeline)) {
      SH.note("container", "Nothing recorded for this selection.");
      $("#share").empty();
      return;
    }
    SH.draw("container", data.timeline, {
      type: "area",
      stacking: "auto",
      title: page.spec.title,
      subtitle: "Read out of the process ancestry of each shell"
    });
    Highcharts.chart("share", {
      chart: { type: "pie", height: 320 },
      palette: { colors: SH.palette },
      title: { text: "In total" },
      tooltip: { pointFormat: "<b>{point.y}</b> commands ({point.percentage:.1f} %)" },
      plotOptions: { pie: { dataLabels: { format: "{point.name}: {point.percentage:.1f} %" }, animation: false } },
      series: [{ name: "Commands", data: data.share.slices }]
    });
  }, page);
});

/* One pie, whichever dimension is being split by. */
$(function () {
  var page = window.CHART;
  SH.start(page.endpoint, function (data) {
    if (!data.slices || !data.slices.length) {
      SH.note("container", "Nothing recorded for this selection.");
      return;
    }
    Highcharts.chart("container", {
      chart: { type: "pie" },
      palette: { colors: SH.palette },
      title: { text: page.spec.title },
      subtitle: {
        text: data.split
          ? data.total.toLocaleString() + " commands by " + data.split
          : data.total.toLocaleString() + " commands — pick something to split by"
      },
      tooltip: { pointFormat: "<b>{point.y}</b> commands ({point.percentage:.1f} %)" },
      plotOptions: {
        pie: { dataLabels: { format: "{point.name}: {point.percentage:.1f} %" }, animation: false }
      },
      series: [{ name: "Commands", data: data.slices }]
    });
  }, page);
});

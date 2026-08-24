/* What tends to follow what. */
$(function () {
  var page = window.CHART;
  SH.start(page.endpoint, function (data) {
    if (!data.series.length) {
      SH.note("container", "Nothing recorded for this selection.");
      return;
    }
    Highcharts.chart("container", {
      chart: { type: "heatmap" },
      title: { text: page.spec.title },
      subtitle: { text: "Read a cell as: the command on the left was followed by the one below" },
      xAxis: { categories: data.categories, opposite: true, labels: { rotation: -45 } },
      yAxis: { categories: data.categories, title: null, reversed: true },
      colorAxis: {
        min: 1,
        minColor: "#dbeaf7",
        maxColor: SH.palette[0],
        type: "logarithmic"
      },
      legend: { align: "right", layout: "vertical", verticalAlign: "middle", symbolHeight: 300 },
      tooltip: {
        formatter: function () {
          return "<b>" + data.categories[this.point.y] + "</b> was followed by <b>" +
            data.categories[this.point.x] + "</b><br>" + this.point.value.toLocaleString() + " times";
        }
      },
      plotOptions: { series: { animation: false } },
      series: [{ name: "Transitions", borderWidth: 1, borderColor: "#f4f4f4", data: data.series }]
    });
  }, page);
});

/* The week as a grid. */
$(function () {
  var page = window.CHART;
  SH.start(page.endpoint, function (data) {
    if (!data.max) {
      SH.note("container", "Nothing recorded for this selection.");
      return;
    }
    Highcharts.chart("container", {
      chart: { type: "heatmap", height: 420 },
      title: { text: page.spec.title },
      subtitle: { text: "Busiest hour: " + data.max.toLocaleString() + " commands" },
      xAxis: { categories: data.hours, title: { text: "Hour of the day" } },
      yAxis: { categories: data.days, title: null, reversed: true },
      colorAxis: { min: 0, minColor: "#ffffff", maxColor: SH.palette[0] },
      legend: { align: "right", layout: "vertical", verticalAlign: "middle", symbolHeight: 280 },
      tooltip: {
        formatter: function () {
          return "<b>" + data.days[this.point.y] + ", " + data.hours[this.point.x] + ":00</b><br>" +
            this.point.value.toLocaleString() + " commands";
        }
      },
      plotOptions: { series: { animation: false } },
      series: [{ name: "Commands", borderWidth: 1, borderColor: "#f4f4f4", data: data.data }]
    });
  }, page);
});

/* Whether failures fail fast. */
$(function () {
  var page = window.CHART;
  SH.start(page.endpoint, function (data) {
    if (!data.categories.length) {
      SH.note("container", "Nothing recorded for this selection.");
      return;
    }
    Highcharts.chart("container", {
      chart: { type: "boxplot" },
      colors: SH.palette,
      title: { text: page.spec.title },
      subtitle: { text: "Whiskers at the 5th and 95th percentiles" },
      xAxis: {
        categories: data.categories.map(function (code, index) {
          return data.meanings[index] ? code + " – " + data.meanings[index] : code;
        })
      },
      yAxis: { title: { text: "Seconds" }, type: "logarithmic", min: 0.01 },
      legend: { enabled: false },
      tooltip: {
        headerFormat: "<em>Exit code {point.key}</em><br>",
        pointFormat:
          "Median <b>{point.median}s</b><br>Quartiles {point.q1}s – {point.q3}s<br>" +
          "5th to 95th {point.low}s – {point.high}s"
      },
      plotOptions: { series: { animation: false } },
      series: [{ name: "Duration", data: data.boxes }]
    });
  }, page);
});

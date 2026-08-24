/* How many goes it takes, and what most often takes more than one. */
$(function () {
  var page = window.CHART;
  SH.start(page.endpoint, function (data) {
    if (SH.empty(data.attempts)) {
      SH.note("container", "Nothing recorded for this selection.");
      $("#worst").empty();
      return;
    }
    SH.draw("container", data.attempts, {
      title: "Attempts per chain",
      xTitle: "Times the same program was run in a row",
      yTitle: "Chains"
    });
    Highcharts.chart("worst", {
      chart: { type: "bar" },
      palette: { colors: SH.palette },
      title: { text: "The programs that take more than one go" },
      subtitle: { text: "Share of chains that needed a second attempt" },
      xAxis: { categories: data.worst.categories },
      yAxis: { min: 0, title: { text: "% of chains retried" } },
      legend: { enabled: false },
      tooltip: {
        formatter: function () {
          var index = data.worst.categories.indexOf(this.x);
          return "<b>" + this.x + "</b><br>" + this.y + " % of " +
            data.worst.chains[index].toLocaleString() + " chains needed another go";
        }
      },
      plotOptions: { series: { animation: false } },
      series: [{ name: "Retried", data: data.worst.rates }]
    });
  }, page);
});

/* One square per day, one strip per year. */
$(function () {
  var page = window.CHART;
  var DAYS = ["Mon", "", "Wed", "", "Fri", "", "Sun"];

  function weekOf(date) {
    var start = new Date(date.getFullYear(), 0, 1);
    // Monday-first week numbering, matching every other chart here.
    var offset = (start.getDay() + 6) % 7;
    return Math.floor((dayOfYear(date) + offset) / 7);
  }

  function dayOfYear(date) {
    var start = new Date(date.getFullYear(), 0, 1);
    return Math.round((date - start) / 86400000);
  }

  SH.start(page.endpoint, function (data) {
    var host = $("#container").empty();
    if (!data.days.length) {
      SH.note("container", "Nothing recorded for this selection.");
      return;
    }
    var years = {};
    data.days.forEach(function (entry) {
      var parts = entry.date.split("-");
      var date = new Date(+parts[0], +parts[1] - 1, +parts[2]);
      var year = date.getFullYear();
      (years[year] = years[year] || []).push([weekOf(date), (date.getDay() + 6) % 7, entry.count, entry.date]);
    });

    $("<p>").addClass("sh-about").text(
      data.total.toLocaleString() + " commands over " + data.active.toLocaleString() +
      " days, busiest day " + data.max.toLocaleString()
    ).appendTo(host);

    Object.keys(years).sort().forEach(function (year) {
      var element = $("<div>").addClass("sh-calendar-year").appendTo(host)[0];
      Highcharts.chart(element, {
        chart: { type: "heatmap", height: 170, marginTop: 30, marginBottom: 20 },
        title: { text: String(year), align: "left", style: { fontSize: "14px" } },
        xAxis: { min: 0, max: 53, visible: false },
        yAxis: { categories: DAYS, title: null, reversed: true, labels: { style: { fontSize: "9px" } } },
        colorAxis: { min: 0, max: data.max, minColor: "#eeeeee", maxColor: SH.palette[2], type: "logarithmic" },
        legend: { enabled: false },
        tooltip: {
          formatter: function () {
            return "<b>" + this.point.custom + "</b><br>" + this.point.value.toLocaleString() + " commands";
          }
        },
        plotOptions: { series: { animation: false } },
        series: [{
          name: "Commands",
          borderWidth: 1,
          borderColor: "#ffffff",
          data: years[year].map(function (cell) {
            return { x: cell[0], y: cell[1], value: cell[2], custom: cell[3] };
          })
        }]
      });
    });
  }, page);
});

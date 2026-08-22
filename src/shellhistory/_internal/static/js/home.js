/* The front page: the numbers, the week, and the cloud. */
$(function () {
  function tile(value, label) {
    return $("<div>").addClass("sh-tile")
      .append($("<span>").addClass("sh-tile-value").text(value))
      .append($("<span>").addClass("sh-tile-label").text(label));
  }

  function number(value) {
    return (value || 0).toLocaleString();
  }

  $.getJSON("/stats_json", function (data) {
    var tiles = $("#stats").empty();
    if (!data.commands) {
      tiles.append($("<p>").addClass("sh-empty").text(
        "Nothing recorded yet. Source shellhistory.sh from your shell, run a command, and come back."
      ));
      return;
    }
    tiles.append(tile(number(data.commands), "commands"));
    tiles.append(tile(number(data.shells), "shells"));
    tiles.append(tile(number(data.programs), "different programs"));
    tiles.append(tile(number(data.days), "days of history"));
    tiles.append(tile(number(data.activeDays), "days with a command"));
    tiles.append(tile(data.perDay, "commands a day"));
    tiles.append(tile(data.successRate + " %", "worked first time"));
    tiles.append(tile(number(Math.round(data.hours)), "hours spent running them"));
    if (data.topCommand) {
      tiles.append(tile(data.topCommand.name, "most used, " + number(data.topCommand.count) + " times"));
    }
    if (data.busiestDay) {
      tiles.append(tile(data.busiestDay.date, "busiest day, " + number(data.busiestDay.count) + " commands"));
    }
    tiles.append(tile(number(data.longest), "characters in the longest line"));
    tiles.append(tile(String(data.first || "").slice(0, 10), "first command"));
  });

  $.getJSON("/punchcard_json", function (data) {
    if (!data.max) { return; }
    Highcharts.chart("punchcard", {
      chart: { type: "heatmap", height: 380 },
      title: { text: "Your week" },
      xAxis: { categories: data.hours, title: { text: "Hour of the day" } },
      yAxis: { categories: data.days, title: null, reversed: true },
      colorAxis: { min: 0, minColor: "#ffffff", maxColor: SH.palette[0] },
      legend: { enabled: false },
      tooltip: {
        formatter: function () {
          return "<b>" + data.days[this.point.y] + ", " + data.hours[this.point.x] + ":00</b><br>" +
            this.point.value.toLocaleString() + " commands";
        }
      },
      plotOptions: { series: { animation: false } },
      series: [{ name: "Commands", borderWidth: 1, borderColor: "#f4f4f4", data: data.data }]
    });
  });

  $.getJSON("/wordcloud_json", function (data) {
    if (!data.words.length) { return; }
    Highcharts.chart("container", {
      chart: { height: 380 },
      title: { text: "What you type" },
      series: [{ type: "wordcloud", name: "Times used", data: data.words }],
      tooltip: { pointFormat: "<b>{point.name}</b>: {point.weight} times" },
      plotOptions: { series: { animation: false } }
    });
  });
});

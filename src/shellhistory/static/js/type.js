$(document).ready(function () {

  // Forward the page's query string, so /type?normalize=1 asks the endpoint to
  // fold Zsh's type names onto Bash's before the slices are counted.
  $.getJSON('/type_json' + window.location.search, function (data) {

    Highcharts.chart('container', {
      chart: {
        plotBackgroundColor: null,
        plotBorderWidth: null,
        plotShadow: false,
        type: 'pie'
      },
      title: {
        text: 'Commands by type'
      },
      tooltip: {
        pointFormat: '{series.name}: <b>{point.y}</b>'
      },
      legend: {},
      plotOptions: {
        pie: {
          allowPointSelect: true,
          cursor: 'pointer',
          dataLabels: {
            enabled: true,
            format: '<b>{point.name}</b>: {point.percentage:.1f} %',
            style: {
              color: (Highcharts.theme && Highcharts.theme.contrastTextColor) || 'black'
            }
          },
          showInLegend: true
        }
      },
      series: [{
        name: 'Commands',
        colorByPoint: true,
        data: data
      }]
    });

  });

});

$(document).ready(function () {
  // Highcharts sizes a word as `weight / maxWeight * maxFontSize`, clamped at
  // `minFontSize`. A single very frequent command (`make`, `git`...) therefore
  // squashes every other word onto the floor. We compensate by mapping the
  // occurrence counts through a logarithm and spreading the result over the
  // whole font size range: weights are expressed directly in pixels, so the
  // rarest word still gets MIN_FONT_SIZE and the most frequent one
  // MAX_FONT_SIZE, however wide the gap between their counts is.
  var MIN_FONT_SIZE = 9;
  var MAX_FONT_SIZE = 36;

  // The server already counts the words (first word of each command only).
  $.getJSON('/wordcloud_json', function (words) {
    var logs = words.map(function (word) {
      return Math.log(word.count);
    });
    var minLog = Math.min.apply(null, logs);
    var maxLog = Math.max.apply(null, logs);
    var logSpan = maxLog - minLog;

    var data = words.map(function (word, index) {
      // All words equally frequent: no gap to compensate, show them all large.
      var ratio = logSpan > 0 ? (logs[index] - minLog) / logSpan : 1;
      return {
        name: word.name,
        weight: MIN_FONT_SIZE + ratio * (MAX_FONT_SIZE - MIN_FONT_SIZE)
      };
    });

    Highcharts.chart('container', {
      title: {
        text: null
      },
      series: [{
        type: 'wordcloud',
        data: data,
        name: 'Occurrences',
        minFontSize: MIN_FONT_SIZE,
        maxFontSize: MAX_FONT_SIZE,
        enableMouseTracking: false
      }],
      exporting: {
        enabled: false
      },
      credits: {
        enabled: false
      }
    });
  });
});

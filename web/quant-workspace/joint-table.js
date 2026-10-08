// Exact arithmetic on an already frozen count table; no sampling or fitting.
export function jointFrequencyViews(joint) {
  const counts = joint?.counts, total = joint?.sampleCount;
  if (!Array.isArray(counts) || !counts.length || !Array.isArray(counts[0]) || !counts[0].length ||
      !Number.isSafeInteger(total) || total < 0) return null;
  const columns = counts[0].length;
  if (!counts.every(row => Array.isArray(row) && row.length === columns &&
      row.every(n => Number.isSafeInteger(n) && n >= 0))) return null;
  const rowCounts = counts.map(row => row.reduce((sum, n) => sum + n, 0));
  const columnCounts = Array.from({length: columns}, (_, i) => counts.reduce((sum, row) => sum + row[i], 0));
  if (rowCounts.reduce((sum, n) => sum + n, 0) !== total) return null;
  const divide = (n, d) => d > 0 ? n / d : null;
  return {
    rowCounts, columnCounts,
    rowProbabilities: rowCounts.map(n => divide(n, total)),
    columnProbabilities: columnCounts.map(n => divide(n, total)),
    yGivenX: counts.map((row, i) => row.map(n => divide(n, rowCounts[i]))),
    xGivenY: counts.map(row => row.map((n, i) => divide(n, columnCounts[i])))
  };
}

type NetworkInformationLike = {
  effectiveType?: string;
  saveData?: boolean;
};

type NavigatorWithConnection = Navigator & {
  connection?: NetworkInformationLike;
};

function connectionInfo() {
  if (typeof navigator === 'undefined') return null;
  return (navigator as NavigatorWithConnection).connection || null;
}

export function canPrefetchNonCritical() {
  const connection = connectionInfo();
  if (!connection) return true;
  if (connection.saveData) return false;
  return connection.effectiveType !== 'slow-2g' && connection.effectiveType !== '2g';
}

export function canPrefetchLargeMedia() {
  const connection = connectionInfo();
  if (!connection) return true;
  if (connection.saveData) return false;
  return !['slow-2g', '2g', '3g'].includes(connection.effectiveType || '');
}

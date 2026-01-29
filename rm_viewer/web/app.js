const FOLDER_ICON = `<svg xmlns="http://www.w3.org/2000/svg" width="48" viewBox="0 0 48 48" fill="currentColor">
  <path d="M21.9891 7L24.9891 14H45.5V41H3.5V7H21.9891ZM21.7252 14L20.0109 10H8C7.17157 10 6.5 10.6716 6.5 11.5V24C6.5 18.4772 10.9772 14 16.5 14H21.7252Z"></path>
</svg>`;

const FOLDER_EMPTY_ICON = `<svg xmlns="http://www.w3.org/2000/svg" width="48" viewBox="0 0 48 48" fill="currentColor">
  <path d="M3 7H21.4891L24.4891 14H45V41H3V7ZM16.5 17C10.701 17 6 21.701 6 27.5V38H42V17H16.5ZM19.5109 10H7.5C6.67157 10 6 10.6716 6 11.5V14H21.2252L19.5109 10Z"></path>
</svg>`;

function renderFolders(folders) {
  const grid = document.getElementById('folder_grid');
  grid.innerHTML = '';
  
  folders.forEach(folder => {
    const btn = document.createElement('button');
    btn.className = 'folder';
    btn.innerHTML = `
      ${folder.empty ? FOLDER_EMPTY_ICON : FOLDER_ICON}
      <span>${folder.name}</span>
    `;
    grid.appendChild(btn);
  });
}

// Usage:
renderFolders([
  { name: 'Articles', empty: false },
  { name: 'Books', empty: false },
  { name: 'Comics', empty: false },
  { name: 'Development methadologies', empty: false },
  // { name: 'Develot ', empty: false },
  { name: 'Papers', empty: false },
  { name: 'Recipes', empty: true },
]);

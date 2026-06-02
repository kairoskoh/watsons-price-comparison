let products = [];
const tbody = document.querySelector('#price-table tbody');
const searchInput = document.getElementById('search-input');
const pageSizeSelect = document.getElementById('page-size-select');
const paginationContainer = document.getElementById('pagination');
let currentCurrency = localStorage.getItem('currency') || 'SGD';
let exchangeRate = 3.1;
let lastUpdated = new Date();
let currentPage = 1;
let itemsPerPage = 10;
let searchQuery = '';

function parseCSVLine(line) {
  const result = [];
  let current = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    if (line[i] === '"') {
      inQuotes = !inQuotes;
    } else if (line[i] === ',' && !inQuotes) {
      result.push(current.trim());
      current = '';
    } else {
      current += line[i];
    }
  }
  result.push(current.trim());
  return result;
}

// Extract numeric value from price string
function extractPriceValue(priceStr) {
  if (!priceStr) return 0;
  const match = priceStr.match(/[\d.]+/);
  return match ? parseFloat(match[0]) : 0;
}

// Fetch products.csv — contains only products found in both stores, pre-matched by scraper.py
async function loadProductsFromCSV() {
  try {
    const response = await fetch('products.csv');
    const csvText = await response.text();
    const lines = csvText.trim().split('\n');

    const loaded = [];
    for (let i = 1; i < lines.length; i++) {
      const line = lines[i].trim();
      if (!line) continue;
      const cols = parseCSVLine(line);
      if (cols.length < 3) continue;
      // Columns: name, sg_price, jb_price[, sg_url, jb_url]
      const [name, sgStr, jbStr, sgUrl = '', jbUrl = ''] = cols;
      const sgPrice = extractPriceValue(sgStr);
      const jbPrice = extractPriceValue(jbStr);
      if (!sgPrice || !jbPrice) continue;
      loaded.push({
        name,
        watsons_sg_price: sgPrice,
        watsons_sg_str: sgStr,
        watsons_jb_price: jbPrice,
        watsons_jb_str: jbStr,
        sg_url: sgUrl,
        jb_url: jbUrl,
      });
    }

    products = loaded;
    lastUpdated = new Date();
    updateLastUpdatedDisplay();
    renderTable();
    console.log(`Loaded ${products.length} products from CSV`);
  } catch (error) {
    console.error('Error loading CSV:', error);
  }
}

function updateLastUpdatedDisplay() {
  const lastUpdatedEl = document.getElementById('last-updated');
  const now = new Date();
  const timeString = now.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  });
  lastUpdatedEl.textContent = `Last updated: ${timeString}`;
}

function formatCurrency(value, currency) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    maximumFractionDigits: 2
  }).format(value);
}

function calculatePriceDifference(sgPrice, jbPrice) {
  const diff = Math.abs(sgPrice - jbPrice);
  let text, isJBCheaper = false;
  
  if (sgPrice < jbPrice) {
    text = `SG is cheaper by ${formatCurrency(diff, currentCurrency)}`;
  } else if (jbPrice < sgPrice) {
    text = `JB is cheaper by ${formatCurrency(diff, currentCurrency)}`;
    isJBCheaper = true;
  } else {
    text = 'Same price';
  }
  
  return { text, isJBCheaper, diff };
}

function convertPrice(sgPrice, jbPrice, displayCurrency) {
  if (displayCurrency === 'SGD') {
    return { sg: sgPrice, jb: jbPrice / exchangeRate };
  } else {
    return { sg: sgPrice * exchangeRate, jb: jbPrice };
  }
}

function getMaxPriceDifference(product, currency) {
  const prices = convertPrice(product.watsons_sg_price, product.watsons_jb_price, currency);
  return Math.abs(prices.sg - prices.jb);
}

function renderTable() {
  tbody.innerHTML = '';
  
  const sortedProducts = [...products].sort((a, b) => {
    const aHasBoth = (a.watsons_sg_price && a.watsons_jb_price) ? 1 : 0;
    const bHasBoth = (b.watsons_sg_price && b.watsons_jb_price) ? 1 : 0;
    if (aHasBoth !== bHasBoth) return bHasBoth - aHasBoth;
    if (aHasBoth && bHasBoth) {
      return getMaxPriceDifference(b, currentCurrency) - getMaxPriceDifference(a, currentCurrency);
    }
    return a.name.localeCompare(b.name);
  });

  const filteredProducts = sortedProducts.filter(product => {
    return product.name.toLowerCase().includes(searchQuery.toLowerCase());
  });

  const totalProducts = filteredProducts.length;
  const totalPages = Math.max(1, Math.ceil(totalProducts / itemsPerPage));
  if (currentPage > totalPages) currentPage = totalPages;

  const startIndex = (currentPage - 1) * itemsPerPage;
  const pageProducts = filteredProducts.slice(startIndex, startIndex + itemsPerPage);

  if (pageProducts.length === 0) {
    const emptyRow = document.createElement('tr');
    emptyRow.innerHTML = `
      <td colspan="5" style="text-align:center; padding: 1.5rem; color: #6b7280;">
        No products match your search.
      </td>
    `;
    tbody.appendChild(emptyRow);
  } else {
    pageProducts.forEach(product => {
      const prices = convertPrice(product.watsons_sg_price, product.watsons_jb_price, currentCurrency);
      const diff = calculatePriceDifference(prices.sg, prices.jb);
      const sgDisplay = formatCurrency(prices.sg, currentCurrency);
      const jbDisplay = formatCurrency(prices.jb, currentCurrency);
      const diffText = diff.text;
      const isJBCheaper = diff.isJBCheaper;

      const row = document.createElement('tr');
      const diff_cell = document.createElement('td');
      diff_cell.textContent = diffText;
      if (isJBCheaper) {
        diff_cell.classList.add('jb-cheaper');
      }

      const sgCell = product.sg_url
        ? `<a href="${product.sg_url}" target="_blank" rel="noopener">${sgDisplay}</a>`
        : sgDisplay;
      const jbCell = product.jb_url
        ? `<a href="${product.jb_url}" target="_blank" rel="noopener">${jbDisplay}</a>`
        : jbDisplay;

      row.innerHTML = `
        <td>${product.name}</td>
        <td>Watsons</td>
        <td>${sgCell}</td>
        <td>${jbCell}</td>
      `;
      row.appendChild(diff_cell);
      tbody.appendChild(row);
    });
  }

  renderPagination(totalPages);
}

function renderPagination(totalPages) {
  paginationContainer.innerHTML = '';

  const prevButton = document.createElement('button');
  prevButton.type = 'button';
  prevButton.textContent = 'Previous';
  prevButton.className = 'page-btn';
  prevButton.disabled = currentPage === 1;
  prevButton.addEventListener('click', () => {
    if (currentPage > 1) {
      currentPage -= 1;
      renderTable();
    }
  });
  paginationContainer.appendChild(prevButton);

  const pageInfo = document.createElement('span');
  pageInfo.className = 'page-info';
  pageInfo.textContent = `Page ${currentPage} of ${totalPages}`;
  paginationContainer.appendChild(pageInfo);

  const nextButton = document.createElement('button');
  nextButton.type = 'button';
  nextButton.textContent = 'Next';
  nextButton.className = 'page-btn';
  nextButton.disabled = currentPage === totalPages;
  nextButton.addEventListener('click', () => {
    if (currentPage < totalPages) {
      currentPage += 1;
      renderTable();
    }
  });
  paginationContainer.appendChild(nextButton);
}

async function updateExchangeRate() {
  try {
    const response = await fetch('https://api.exchangerate-api.com/v4/latest/SGD');
    const data = await response.json();
    exchangeRate = data.rates.MYR;
    console.log(`Exchange rate: 1 SGD = ${exchangeRate} MYR`);
    renderTable();
  } catch (error) {
    console.log('Using default exchange rate: 1 SGD = 3.1 MYR');
    renderTable();
  }
}

const currencyBtn = document.getElementById('currency-btn');
currencyBtn.addEventListener('click', () => {
  currentCurrency = currentCurrency === 'SGD' ? 'MYR' : 'SGD';
  localStorage.setItem('currency', currentCurrency);
  currencyBtn.textContent = currentCurrency;
  renderTable();
});

searchInput.addEventListener('input', event => {
  searchQuery = event.target.value.trim();
  currentPage = 1;
  renderTable();
});

pageSizeSelect.addEventListener('change', event => {
  itemsPerPage = Math.min(50, parseInt(event.target.value, 10) || 10);
  currentPage = 1;
  renderTable();
});


currencyBtn.textContent = currentCurrency;

async function initializePage() {
  await loadProductsFromCSV();
  await updateExchangeRate();
}

initializePage();

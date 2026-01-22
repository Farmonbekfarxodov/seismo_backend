// =====================================
// Earthquake Map with Leaflet. js
// =====================================

let map;
let markersLayer;
let earthquakeData = [];

// Magnitude bo'yicha rang
function getMagnitudeColor(magnitude) {
    if (magnitude >= 7.0) return '#8B0000'; // Kuchli
    if (magnitude >= 6.0) return '#DC143C';
    if (magnitude >= 5.0) return '#FF4500';
    if (magnitude >= 4.0) return '#FFA500';
    if (magnitude >= 3.0) return '#FFD700';
    if (magnitude >= 2.0) return '#FFFF00';
    return '#90EE90'; // Zaif
}

// Magnitude bo'yicha radius
function getMagnitudeRadius(magnitude) {
    return Math.max(magnitude * 2, 4);
}

// Xarita initsializatsiya
function initMap(lat = 41.2995, lon = 69.2401, zoom = 6) {
    map = L.map('map').setView([lat, lon], zoom);

    // Tile layer
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors',
        maxZoom: 18,
    }).addTo(map);

    // Marker cluster
    markersLayer = L. markerClusterGroup({
        maxClusterRadius: 50,
        spiderfyOnMaxZoom:  true,
        showCoverageOnHover: false,
    });

    map.addLayer(markersLayer);

    // Fullscreen
    map.addControl(new L.Control.Fullscreen());

    // Scale
    L.control.scale().addTo(map);
}

// Marker qo'shish
function addEarthquakeMarker(eq) {
    const lat = parseFloat(eq.latitude);
    const lon = parseFloat(eq.longitude);
    const mag = parseFloat(eq.magnitude);
    const depth = parseFloat(eq.depth);

    if (isNaN(lat) || isNaN(lon)) return;

    const marker = L.circleMarker([lat, lon], {
        radius: getMagnitudeRadius(mag),
        fillColor: getMagnitudeColor(mag),
        color: '#000',
        weight: 1,
        opacity: 1,
        fillOpacity: 0.7
    });

    const popupContent = `
        <div style="font-family: Arial; min-width: 200px;">
            <h4 style="margin: 0 0 10px 0;">Zilzila Ma'lumotlari</h4>
            <table style="width: 100%; font-size: 12px;">
                <tr>
                    <td><b>Magnituda:</b></td>
                    <td><span style="color: ${getMagnitudeColor(mag)}; font-weight: bold;">${mag.toFixed(1)}</span></td>
                </tr>
                <tr>
                    <td><b>Chuqurlik:</b></td>
                    <td>${depth.toFixed(1)} km</td>
                </tr>
                <tr>
                    <td><b>Sana:</b></td>
                    <td>${eq.date}</td>
                </tr>
                <tr>
                    <td><b>Vaqt:</b></td>
                    <td>${eq.time}</td>
                </tr>
                <tr>
                    <td><b>Epitsenter:</b></td>
                    <td>${eq.epicenter || "Noma'lum"}</td>
                </tr>
            </table>
        </div>
    `;

    marker.bindPopup(popupContent);
    markersLayer.addLayer(marker);
}

// API dan ma'lumot yuklash
async function loadEarthquakeData(filters = {}) {
    try {
        showLoading(true);

        const url = new URL('/seismos/api/earthquakes/', window.location.origin);

        if (filters.start_date) url.searchParams.append('start_date', filters.start_date);
        if (filters.end_date) url.searchParams.append('end_date', filters.end_date);
        if (filters.min_magnitude) url.searchParams.append('min_mag', filters.min_magnitude);
        if (filters.max_magnitude) url.searchParams.append('max_mag', filters.max_magnitude);

        const response = await fetch(url);

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        earthquakeData = data. earthquakes || [];

        markersLayer.clearLayers();
        earthquakeData.forEach(eq => addEarthquakeMarker(eq));

        updateStatistics(data.statistics);
        showLoading(false);

        if (earthquakeData.length > 0) {
            map.fitBounds(markersLayer.getBounds(), { padding: [50, 50] });
        }

    } catch (error) {
        console.error('Error loading earthquake data:', error);
        showError('Ma\'lumotlarni yuklashda xatolik yuz berdi.');
        showLoading(false);
    }
}

// Statistikani yangilash
function updateStatistics(stats) {
    if (! stats) return;

    document.getElementById('total-count').textContent = stats.total || 0;
    document.getElementById('max-magnitude').textContent = stats. max_magnitude?. toFixed(1) || 'N/A';
    document. getElementById('avg-magnitude').textContent = stats. avg_magnitude?.toFixed(2) || 'N/A';
    document.getElementById('date-range').textContent =
        `${stats.start_date || 'N/A'} - ${stats.end_date || 'N/A'}`;
}

// Loading indicator
function showLoading(show) {
    const loader = document.getElementById('loading-indicator');
    if (loader) {
        loader.style. display = show ? 'flex' : 'none';
    }
}

// Error message
function showError(message) {
    const errorDiv = document.getElementById('error-message');
    if (errorDiv) {
        errorDiv.textContent = message;
        errorDiv.style.display = 'block';
        setTimeout(() => {
            errorDiv.style.display = 'none';
        }, 5000);
    }
}

// Filter qo'llash
function applyFilters() {
    const filters = {
        start_date:  document.getElementById('filter-start-date')?.value,
        end_date: document.getElementById('filter-end-date')?.value,
        min_magnitude: document.getElementById('filter-min-mag')?.value,
        max_magnitude: document.getElementById('filter-max-mag')?.value
    };

    loadEarthquakeData(filters);
}

// Filtrlarni tozalash
function clearFilters() {
    document.getElementById('filter-start-date').value = '';
    document.getElementById('filter-end-date').value = '';
    document.getElementById('filter-min-mag').value = '';
    document.getElementById('filter-max-mag').value = '';
    loadEarthquakeData();
}

// Sahifa yuklanganda
document.addEventListener('DOMContentLoaded', function() {
    initMap();
    loadEarthquakeData();

    const applyBtn = document.getElementById('apply-filters');
    if (applyBtn) {
        applyBtn.addEventListener('click', applyFilters);
    }

    const clearBtn = document.getElementById('clear-filters');
    if (clearBtn) {
        clearBtn.addEventListener('click', clearFilters);
    }
});
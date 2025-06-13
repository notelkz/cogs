// Twitch API credentials
const clientId = 'alqrvc19w30xtrbg0j7f3r1ej5k852';
const clientSecret = '4oe2bqiyk5nnfjcb1q10x06oyn1nwm';

// Get elements
const usernameInput = document.getElementById('username-input');
const loadButton = document.getElementById('load-button');
const subscriberImage = document.getElementById('subscriber-image');
const subscriberName = document.getElementById('subscriber-name');
const subDate = document.getElementById('sub-date');

// Add event listener to the button
loadButton.addEventListener('click', () => {
    const username = usernameInput.value.trim();
    if (username) {
        loadUserData(username);
    }
});

// Function to get Twitch access token
async function getTwitchAccessToken() {
    try {
        const response = await fetch('https://id.twitch.tv/oauth2/token', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: new URLSearchParams({
                client_id: clientId,
                client_secret: clientSecret,
                grant_type: 'client_credentials'
            })
        });
        
        const data = await response.json();
        return data.access_token;
    } catch (error) {
        console.error('Error getting access token:', error);
        return null;
    }
}

// Function to get user profile data
async function getUserProfileData(username) {
    try {
        const accessToken = await getTwitchAccessToken();
        if (!accessToken) return null;
        
        const response = await fetch(`https://api.twitch.tv/helix/users?login=${username}`, {
            headers: {
                'Client-ID': clientId,
                'Authorization': `Bearer ${accessToken}`
            }
        });
        
        const data = await response.json();
        if (data.data && data.data.length > 0) {
            return {
                profileImage: data.data[0].profile_image_url,
                displayName: data.data[0].display_name
            };
        } else {
            return null;
        }
    } catch (error) {
        console.error('Error fetching user data:', error);
        return null;
    }
}

// Function to load user data and update the card
async function loadUserData(username) {
    // Show loading state
    subscriberName.textContent = 'Loading...';
    subscriberImage.src = 'https://static-cdn.jtvnw.net/jtv_user_pictures/placeholder_profile_image-70x70.png';
    
    const userData = await getUserProfileData(username);
    
    if (userData) {
        // Update the card with user data
        subscriberImage.src = userData.profileImage;
        subscriberName.textContent = userData.displayName;
        
        // Update the subscription date with current date
        const now = new Date();
        const dateString = now.toLocaleDateString('en-US', { 
            year: 'numeric', 
            month: 'long', 
            day: 'numeric' 
        });
        subDate.textContent = `${dateString} • #${Math.floor(Math.random() * 100)}/∞`;
    } else {
        subscriberName.textContent = 'User not found';
    }
}

// Load a default user when the page loads (optional)
// loadUserData('elkz');

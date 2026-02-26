// ══════════════════════════════════════════════════════════════════════════
//  Authentication Module
// ══════════════════════════════════════════════════════════════════════════

const authModule = (() => {
    const API = '/api/auth';
    
    // DOM elements
    const modalOverlay = document.getElementById('auth-modal-overlay');
    const modalClose = document.getElementById('auth-modal-close');
    const signinForm = document.getElementById('auth-signin-form');
    const signupForm = document.getElementById('auth-signup-form');
    const showSignupBtn = document.getElementById('show-signup');
    const showSigninBtn = document.getElementById('show-signin');
    
    // Sign in elements
    const signinEmail = document.getElementById('signin-email');
    const signinPassword = document.getElementById('signin-password');
    const signinSubmit = document.getElementById('signin-submit');
    const signinSubmitText = document.getElementById('signin-submit-text');
    const signinSubmitSpinner = document.getElementById('signin-submit-spinner');
    const signinError = document.getElementById('signin-error');
    
    // Sign up elements
    const signupName = document.getElementById('signup-name');
    const signupEmail = document.getElementById('signup-email');
    const signupPassword = document.getElementById('signup-password');
    const signupSubmit = document.getElementById('signup-submit');
    const signupSubmitText = document.getElementById('signup-submit-text');
    const signupSubmitSpinner = document.getElementById('signup-submit-spinner');
    const signupError = document.getElementById('signup-error');
    
    // User UI elements
    const userBar = document.getElementById('user-bar');
    const userInfo = document.getElementById('user-info');
    const userName = document.getElementById('user-name');
    const userAvatar = document.getElementById('user-avatar-btn');
    const userDropdown = document.getElementById('user-dropdown');
    const userDropdownName = document.getElementById('user-dropdown-name');
    const userDropdownEmail = document.getElementById('user-dropdown-email');
    const userDropdownAvatar = document.getElementById('user-dropdown-avatar');
    const userSigninBtn = document.getElementById('user-signin-btn');
    const logoutBtn = document.getElementById('user-dropdown-logout');
    
    // State
    let currentUser = null;
    let authToken = null;
    
    // ── Helpers ───────────────────────────────────────────────────────────
    
    function getToken() {
        if (authToken) return authToken;
        authToken = localStorage.getItem('hyphae_auth_token');
        return authToken;
    }
    
    function setToken(token) {
        authToken = token;
        localStorage.setItem('hyphae_auth_token', token);
    }
    
    function clearToken() {
        authToken = null;
        localStorage.removeItem('hyphae_auth_token');
    }
    
    async function api(url, opts = {}) {
        const token = getToken();
        const headers = { 'Content-Type': 'application/json' };
        if (token) headers['Authorization'] = `Bearer ${token}`;
        
        try {
            const r = await fetch(url, { ...opts, headers });
            if (!r.ok) {
                const text = await r.text();
                throw new Error(text || r.statusText);
            }
            return await r.json();
        } catch (e) {
            console.error('[Auth]', e);
            throw e;
        }
    }
    
    function getInitials(name) {
        if (!name) return '?';
        const parts = name.trim().split(/\s+/);
        if (parts.length === 1) return parts[0][0].toUpperCase();
        return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    }
    
    // ── UI Functions ──────────────────────────────────────────────────────
    
    function showModal() {
        if (modalOverlay) {
            modalOverlay.classList.add('show');
        }
    }
    
    function hideModal() {
        if (modalOverlay) {
            modalOverlay.classList.remove('show');
        }
    }
    
    function showSigninForm() {
        if (signinForm) signinForm.style.display = '';
        if (signupForm) signupForm.style.display = 'none';
        clearErrors();
    }
    
    function showSignupForm() {
        if (signinForm) signinForm.style.display = 'none';
        if (signupForm) signupForm.style.display = '';
        clearErrors();
    }
    
    function clearErrors() {
        if (signinError) signinError.style.display = 'none';
        if (signupError) signupError.style.display = 'none';
    }
    
    function updateUI(user) {
        currentUser = user;
        
        if (user) {
            const initials = getInitials(user.name);
            if (userName) userName.textContent = user.name;
            if (userAvatar) { userAvatar.textContent = initials; userAvatar.style.display = ''; }
            if (userDropdownName) userDropdownName.textContent = user.name;
            if (userDropdownEmail) userDropdownEmail.textContent = user.email;
            if (userDropdownAvatar) userDropdownAvatar.textContent = initials;
            
            if (userInfo) userInfo.style.display = '';
            if (userSigninBtn) userSigninBtn.style.display = 'none';
        } else {
            if (userAvatar) userAvatar.style.display = 'none';
            if (userInfo) userInfo.style.display = 'none';
            if (userSigninBtn) userSigninBtn.style.display = '';
        }
    }
    
    // ── Auth Actions ──────────────────────────────────────────────────────
    
    async function doSignin(email, password) {
        try {
            if (signinSubmitText) signinSubmitText.style.display = 'none';
            if (signinSubmitSpinner) signinSubmitSpinner.style.display = '';
            if (signinSubmit) signinSubmit.disabled = true;
            
            const data = await api(`${API}/login`, {
                method: 'POST',
                body: JSON.stringify({ email, password })
            });
            
            setToken(data.token);
            updateUI(data.user);
            hideModal();
            
            if (signinEmail) signinEmail.value = '';
            if (signinPassword) signinPassword.value = '';
        } catch (e) {
            if (signinError) {
                signinError.textContent = e.message || 'Failed to sign in';
                signinError.style.display = '';
            }
        } finally {
            if (signinSubmitText) signinSubmitText.style.display = '';
            if (signinSubmitSpinner) signinSubmitSpinner.style.display = 'none';
            if (signinSubmit) signinSubmit.disabled = false;
        }
    }
    
    async function doSignup(name, email, password) {
        try {
            if (signupSubmitText) signupSubmitText.style.display = 'none';
            if (signupSubmitSpinner) signupSubmitSpinner.style.display = '';
            if (signupSubmit) signupSubmit.disabled = true;
            
            const data = await api(`${API}/signup`, {
                method: 'POST',
                body: JSON.stringify({ name, email, password })
            });
            
            setToken(data.token);
            updateUI(data.user);
            hideModal();
            
            if (signupName) signupName.value = '';
            if (signupEmail) signupEmail.value = '';
            if (signupPassword) signupPassword.value = '';
        } catch (e) {
            if (signupError) {
                signupError.textContent = e.message || 'Failed to create account';
                signupError.style.display = '';
            }
        } finally {
            if (signupSubmitText) signupSubmitText.style.display = '';
            if (signupSubmitSpinner) signupSubmitSpinner.style.display = 'none';
            if (signupSubmit) signupSubmit.disabled = false;
        }
    }
    
    async function doLogout() {
        try {
            await api(`${API}/logout`, { method: 'POST' });
        } catch (e) {
            console.warn('Logout failed:', e);
        } finally {
            clearToken();
            currentUser = null;
            updateUI(null);
            if (userDropdown) userDropdown.classList.remove('show');
        }
    }
    
    async function checkAuth() {
        const token = getToken();
        if (!token) {
            updateUI(null);
            return;
        }
        
        try {
            const user = await api(`${API}/me`);
            updateUI(user);
        } catch (e) {
            console.warn('Auth check failed:', e);
            clearToken();
            updateUI(null);
        }
    }
    
    // ── Event Listeners ───────────────────────────────────────────────────
    
    modalClose?.addEventListener('click', hideModal);
    modalOverlay?.addEventListener('click', (e) => {
        if (e.target === modalOverlay) hideModal();
    });
    
    showSignupBtn?.addEventListener('click', showSignupForm);
    showSigninBtn?.addEventListener('click', showSigninForm);
    
    signinSubmit?.addEventListener('click', () => {
        const email = signinEmail?.value.trim();
        const password = signinPassword?.value;
        if (email && password) doSignin(email, password);
    });
    
    signinPassword?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            const email = signinEmail?.value.trim();
            const password = signinPassword?.value;
            if (email && password) doSignin(email, password);
        }
    });
    
    signupSubmit?.addEventListener('click', () => {
        const name = signupName?.value.trim();
        const email = signupEmail?.value.trim();
        const password = signupPassword?.value;
        if (name && email && password) doSignup(name, email, password);
    });
    
    signupPassword?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            const name = signupName?.value.trim();
            const email = signupEmail?.value.trim();
            const password = signupPassword?.value;
            if (name && email && password) doSignup(name, email, password);
        }
    });
    
    userAvatar?.addEventListener('click', (e) => {
        e.stopPropagation();
        if (currentUser) {
            userDropdown?.classList.toggle('show');
        }
    });
    
    document.addEventListener('click', (e) => {
        if (userDropdown && !userBar?.contains(e.target)) {
            userDropdown.classList.remove('show');
        }
    });
    
    userSigninBtn?.addEventListener('click', () => {
        showSigninForm();
        showModal();
    });
    
    logoutBtn?.addEventListener('click', doLogout);
    
    // ── Init ──────────────────────────────────────────────────────────────
    
    checkAuth();
    
    return {
        checkAuth,
        showSignin: () => { showSigninForm(); showModal(); },
        showSignup: () => { showSignupForm(); showModal(); },
        logout: doLogout,
        getUser: () => currentUser,
        getToken
    };
})();

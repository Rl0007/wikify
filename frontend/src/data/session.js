import { computed, reactive, ref } from "vue";
import { useCall } from "frappe-ui";
import router from "@/router";

export const sessionUser = ref(getSessionUserFromCookie());
export const sessionFullName = ref(readCookie("full_name") || "");

export const session = reactive({
	login: useCall({
		url: "/api/v2/method/login",
		immediate: false,
		onSuccess(data) {
			sessionUser.value = getSessionUserFromCookie();
			sessionFullName.value = readCookie("full_name") || "";
			session.login.reset();
			router.replace(data?.default_route || "/");
		},
	}),
	logout: useCall({
		url: "/api/v2/method/logout",
		method: "POST",
		immediate: false,
		onSuccess() {
			sessionUser.value = getSessionUserFromCookie();
			window.location.href = "/login";
		},
	}),
	user: sessionUser,
	isLoggedIn: computed(() => sessionUser.value != null),
});

function readCookie(name) {
	const cookies = new URLSearchParams(document.cookie.split("; ").join("&"));
	return cookies.get(name);
}

function getSessionUserFromCookie() {
	let user = readCookie("user_id");
	if (user === "Guest") {
		user = null;
	}
	return user;
}

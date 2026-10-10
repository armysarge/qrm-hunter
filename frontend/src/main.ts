import { mount } from "svelte";
import "@fontsource-variable/roboto-flex";
import "material-symbols/rounded.css";
import "./styles/theme.css";
import App from "./App.svelte";

export default mount(App, { target: document.getElementById("app")! });

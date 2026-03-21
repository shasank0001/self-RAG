package selfrag.container

container := input[0] {
  is_array(input)
  count(input) > 0
}

container := input {
  not is_array(input)
}

deny[msg] {
  not container.Config.Cmd
  msg := "Container image must define CMD"
}

deny[msg] {
  not container.Config.User
  msg := "Container image must set a non-root USER"
}

deny[msg] {
  container.Config.User == "root"
  msg := "Container image cannot run as root"
}

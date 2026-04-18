module spin-go-monolith

go 1.26

require (
	github.com/julienschmidt/httprouter v1.3.0
	github.com/spinframework/spin-go-sdk/v3 v3.0.0-20260417020246-e44427ee1b9c
)

require (
	github.com/apparentlymart/go-userdirs v0.0.0-20200915174352-b0c018a67c13 // indirect
	github.com/bytecodealliance/componentize-go v0.3.2 // indirect
	github.com/gofrs/flock v0.13.0 // indirect
	go.bytecodealliance.org/pkg v0.2.1 // indirect
	golang.org/x/sys v0.43.0 // indirect
)

// replace github.com/spinframework/spin-go-sdk/v3 => ../../

tool github.com/bytecodealliance/componentize-go

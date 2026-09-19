// Exodus authorisation policies. Cedar is deny-by-default: an action is
// allowed only if some permit matches and no forbid matches.

// 1. Only IIITB addresses may create a request.
permit(principal, action == Action::"CreateRequest", resource)
  when { principal.email like "*@iiitb.ac.in" };

// 2. Only members of a group may accept or decline its proposal.
permit(principal, action == Action::"RespondToProposal", resource)
  when { principal in resource.members };

// 3. Other members' contact details are visible only once the group is CONFIRMED.
permit(principal, action == Action::"ViewContactDetails", resource)
  when { principal in resource.members && resource.state == "CONFIRMED" };

// 4. A group containing a blocked pair may never be formed. The permit is
// what the forbid subtracts from -- without it every group would be denied.
permit(principal, action == Action::"FormGroup", resource);

forbid(principal, action == Action::"FormGroup", resource)
  when { resource.contains_blocked_pair };
